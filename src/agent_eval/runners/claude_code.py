"""Claude Code adapter — shells out to `claude -p` in the prepared workspace.

Why the CLI and not the API: the harness under evaluation *is* the CLI's own
configuration surface (AGENTS.md/CLAUDE.md, .claude/skills, settings, MCP servers). If
we drove the model through the API we would have to reimplement how the CLI consumes
those files, and would then be evaluating our reimplementation rather than the harness
the team actually ships.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..redact import redact
from .base import AgentActivity, AgentRunner, AgentRunRequest, AgentRunResult, AgentUsage

# How long to wait for the output readers after the process has exited or been killed.
# A grandchild that inherited the pipe can keep it open; a stuck display must never turn
# into a stuck evaluation.
_READER_GRACE_SECONDS = 10


class _ActivityTracker:
    """Turns stream-json events into one-line "what is the agent doing" summaries."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = str(workspace).rstrip("/") + "/"
        self.turn = 0
        self._message_ids: set[str] = set()

    def _short_path(self, value: str) -> str:
        return value[len(self.workspace):] if value.startswith(self.workspace) else value

    def _describe_tool(self, name: str, tool_input: dict) -> str:
        for key in ("file_path", "notebook_path", "path"):
            if isinstance(tool_input.get(key), str):
                return f"{name} {self._short_path(tool_input[key])}"
        if isinstance(tool_input.get("command"), str):
            lines = tool_input["command"].strip().splitlines() or [""]
            return f"{name} $ {lines[0].replace(self.workspace, '')[:60]}"
        if isinstance(tool_input.get("pattern"), str):
            return f"{name} {tool_input['pattern'][:60]}"
        return name

    def feed(self, line: str) -> AgentActivity | None:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            return AgentActivity(turn=0, summary="agent started")
        if kind == "result":
            return AgentActivity(turn=self.turn, summary="agent finished")
        if kind != "assistant":
            return None
        message = event.get("message") or {}
        message_id = message.get("id")
        if message_id and message_id not in self._message_ids:
            self._message_ids.add(message_id)
            self.turn += 1
        summary = None
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                summary = self._describe_tool(str(block.get("name")), block.get("input") or {})
            elif block.get("type") == "text" and summary is None:
                summary = "writing"
            elif block.get("type") == "thinking" and summary is None:
                summary = "thinking"
        return AgentActivity(turn=self.turn, summary=summary) if summary else None


class ClaudeCodeRunner(AgentRunner):
    """Runs one task attempt with the `claude` CLI in headless mode."""

    name = "claude-code"

    def __init__(
        self,
        *,
        executable: str = "claude",
        output_format: str = "stream-json",
        permission_mode: str | None = "acceptEdits",
        extra_args: list[str] | None = None,
    ) -> None:
        # stream-json rather than json: the single json payload only arrives when the agent
        # exits, so a 90-second run showed nothing at all. The stream carries the same final
        # `result` event (usage, cost, errors) plus every turn before it, which feeds the
        # live progress display and leaves a full per-run transcript in stdout.log.
        # The native installer puts the launcher at ~/.local/bin/claude, so a config
        # naming it that way is the common case. subprocess does not expand `~`, and the
        # resulting failure ("executable not found") points at the wrong problem.
        self.executable = os.path.expanduser(executable)
        self.output_format = output_format
        self.permission_mode = permission_mode
        self.extra_args = list(extra_args or [])

    # -- helpers ----------------------------------------------------------

    def version(self) -> str:
        try:
            proc = subprocess.run(
                [self.executable, "--version"], capture_output=True, text=True, timeout=20
            )
            return proc.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def build_argv(self, request: AgentRunRequest) -> list[str]:
        """Build the command line for one run.

        `permission_mode` and `extra_args` come from the arm's `harness.yaml` when it
        declares them, because they are part of the harness under evaluation - the
        constructor values are only the fallback for a harness that says nothing.
        """
        permission_mode = request.config.get("permission_mode", self.permission_mode)
        extra_args = request.config.get("extra_args") or self.extra_args

        argv = [self.executable, "-p", request.prompt]
        if self.output_format:
            argv += ["--output-format", self.output_format]
            # `claude -p` refuses stream-json without --verbose.
            if self.output_format == "stream-json" and "--verbose" not in extra_args:
                argv.append("--verbose")
        if request.model:
            argv += ["--model", request.model]
        if permission_mode:
            argv += ["--permission-mode", str(permission_mode)]
        return argv + [str(arg) for arg in extra_args]

    @staticmethod
    def infrastructure_error(payload: dict | None, exit_code: int | None) -> str:
        """Detect a run that never happened, as opposed to one that failed.

        Observed in the wild: an expired OAuth session returns exit 1 in under a second
        with `is_error: true`, `terminal_reason: "api_error"`, and a usage block full of
        zeros. Treated naively that is indistinguishable from "the agent thought about it
        and wrote nothing" - it scores as a task failure, and the zero cost gets reported
        as a measured fact. Both are wrong, and both are worse than an error.
        """
        if payload is None:
            if exit_code not in (0, None):
                return f"agent exited {exit_code} without returning a parseable result"
            return ""
        if payload.get("is_error"):
            reason = payload.get("result") or payload.get("terminal_reason") or "unknown"
            return f"agent reported an error: {reason}"
        if payload.get("terminal_reason") in {"api_error", "auth_error", "rate_limit"}:
            return f"agent terminated: {payload['terminal_reason']}"
        return ""

    @staticmethod
    def extract_payload(stdout: str) -> object | None:
        """Find the final payload in either output format.

        ``--output-format json`` prints one JSON document. ``stream-json`` prints one event
        per line and ends with a ``result`` event carrying the same fields; the model name
        only appears in the opening ``init`` event, so it is copied across. A stream with
        no ``result`` event (killed, crashed) has no payload - it is not a run with zero
        usage.
        """
        text = (stdout or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        result: dict | None = None
        model: str | None = None
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                model = event.get("model") or model
            elif event.get("type") == "result":
                result = event
        if result is None:
            return None
        if model and not result.get("model"):
            result = {**result, "model": model}
        return result

    @staticmethod
    def parse_payload(stdout: str) -> tuple[dict | None, AgentUsage, str | None, str | None]:
        """Pull usage out of `--output-format json`, degrading honestly when absent.

        The CLI's payload shape is not ours to control, so every field is optional and a
        missing one yields ``unavailable`` rather than a zero. A zero would silently claim
        the run was free.
        """
        payload = ClaudeCodeRunner.extract_payload(stdout)
        if not isinstance(payload, dict):
            return None, AgentUsage(source="unavailable"), None, None

        raw_usage = payload.get("usage") or {}
        if not isinstance(raw_usage, dict):
            raw_usage = {}
        cost = payload.get("total_cost_usd")
        fields = {
            "input_tokens": raw_usage.get("input_tokens"),
            "output_tokens": raw_usage.get("output_tokens"),
            "cache_read_tokens": raw_usage.get("cache_read_input_tokens"),
            "cache_creation_tokens": raw_usage.get("cache_creation_input_tokens"),
            "cost_usd": cost if isinstance(cost, int | float) else None,
            "turns": payload.get("num_turns"),
        }
        known = any(v is not None for v in fields.values())
        # A run that errored out reports a full usage block of zeros. Those zeros are not
        # a measurement of anything, so they must not be labelled "actual" - see
        # `infrastructure_error`.
        did_work = bool(
            (fields["input_tokens"] or 0) or (fields["output_tokens"] or 0)
            or (fields["cost_usd"] or 0)
        )
        errored = bool(payload.get("is_error"))
        if errored and not did_work:
            usage = AgentUsage(source="unavailable", turns=fields["turns"])
        else:
            usage = AgentUsage(source="actual" if known else "unavailable", **fields)
        return payload, usage, payload.get("model"), payload.get("session_id")

    # -- the interface ----------------------------------------------------

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        argv = self.build_argv(request)
        env = dict(os.environ)
        # The agent must not be able to reach agent-eval's own state.
        env.pop("AGENT_EVAL_RUN_ID", None)

        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv, cwd=request.workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"agent executable not found: {self.executable!r}. Install Claude Code, or "
                f"run with --agent simulated for the offline demo."
            ) from exc

        stdout_lines: list[str] = []
        stderr_chunks: list[str] = []
        tracker = _ActivityTracker(request.workspace)
        on_activity = request.on_activity

        def read_stdout() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                stdout_lines.append(line)
                if on_activity is None:
                    continue
                activity = tracker.feed(line)
                if activity is None:
                    continue
                try:
                    on_activity(activity)
                except Exception:  # noqa: BLE001
                    # A broken progress display must never cost a paid agent run.
                    pass

        def read_stderr() -> None:
            assert proc.stderr is not None
            stderr_chunks.append(proc.stderr.read())

        readers = [
            threading.Thread(target=read_stdout, daemon=True),
            threading.Thread(target=read_stderr, daemon=True),
        ]
        for reader in readers:
            reader.start()

        timed_out = False
        try:
            proc.wait(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
        for reader in readers:
            reader.join(timeout=_READER_GRACE_SECONDS)
        stdout, stderr = "".join(stdout_lines), "".join(stderr_chunks)

        if timed_out:
            return AgentRunResult(
                adapter=self.name, adapter_version=self.version(), exit_code=None,
                timed_out=True, duration_s=time.monotonic() - started,
                stdout=redact(stdout), stderr=redact(stderr),
                usage=AgentUsage(source="unavailable"),
                note=f"timed out after {request.timeout_seconds}s",
            )

        payload, usage, model, session = self.parse_payload(stdout)
        failure = self.infrastructure_error(payload, proc.returncode)
        return AgentRunResult(
            infrastructure_error=failure,
            adapter=self.name, adapter_version=self.version(),
            exit_code=proc.returncode, duration_s=time.monotonic() - started,
            stdout=redact(stdout), stderr=redact(stderr),
            usage=usage, model_reported=model, session_id=session,
            raw=json.loads(redact(json.dumps(payload))) if payload else None,
        )

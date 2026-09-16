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
import time

from ..redact import redact
from .base import AgentRunner, AgentRunRequest, AgentRunResult, AgentUsage


class ClaudeCodeRunner(AgentRunner):
    """Runs one task attempt with the `claude` CLI in headless mode."""

    name = "claude-code"

    def __init__(
        self,
        *,
        executable: str = "claude",
        output_format: str = "json",
        permission_mode: str | None = "acceptEdits",
        extra_args: list[str] | None = None,
    ) -> None:
        self.executable = executable
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
        argv = [self.executable, "-p", request.prompt]
        if self.output_format:
            argv += ["--output-format", self.output_format]
        if request.model:
            argv += ["--model", request.model]
        if self.permission_mode:
            argv += ["--permission-mode", self.permission_mode]
        return argv + self.extra_args

    @staticmethod
    def parse_payload(stdout: str) -> tuple[dict | None, AgentUsage, str | None, str | None]:
        """Pull usage out of `--output-format json`, degrading honestly when absent.

        The CLI's payload shape is not ours to control, so every field is optional and a
        missing one yields ``unavailable`` rather than a zero. A zero would silently claim
        the run was free.
        """
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            return None, AgentUsage(source="unavailable"), None, None
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
            proc = subprocess.run(
                argv, cwd=request.workspace, capture_output=True, text=True,
                timeout=request.timeout_seconds, env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"agent executable not found: {self.executable!r}. Install Claude Code, or "
                f"run with --agent simulated for the offline demo."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            return AgentRunResult(
                adapter=self.name, adapter_version=self.version(), exit_code=None,
                timed_out=True, duration_s=time.monotonic() - started,
                stdout=redact(str(exc.stdout or "")), stderr=redact(str(exc.stderr or "")),
                usage=AgentUsage(source="unavailable"),
                note=f"timed out after {request.timeout_seconds}s",
            )

        payload, usage, model, session = self.parse_payload(proc.stdout)
        return AgentRunResult(
            adapter=self.name, adapter_version=self.version(),
            exit_code=proc.returncode, duration_s=time.monotonic() - started,
            stdout=redact(proc.stdout), stderr=redact(proc.stderr),
            usage=usage, model_reported=model, session_id=session,
            raw=json.loads(redact(json.dumps(payload))) if payload else None,
        )

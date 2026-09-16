"""Replay adapter — re-runs a recorded evaluation without touching an agent.

This is what makes the shipped demo honest. Every real run writes a cassette holding the
patch the agent produced plus its duration and usage; the replay adapter applies that
patch to a fresh workspace and reports the recorded numbers. The objective checks then run
for real against real agent-written code. Nothing is simulated except the waiting.

A randomly-generated fake would make the demo report meaningless. A replay of a real run
does not.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .base import AgentRunner, AgentRunRequest, AgentRunResult, AgentUsage


class CassetteNotFound(Exception):
    """Raised when no recording exists for a requested run."""


def cassette_name(task_id: str, harness_id: str, rep: int) -> str:
    """Cassettes are keyed by what determines a run, not by run id.

    Keying on the harness *content hash* means a cassette is only replayed for the exact
    harness that produced it. Edit the harness and the key changes, so a stale recording
    can never be silently presented as evidence about the new one.
    """
    return f"{task_id}__{harness_id[:12]}__rep{rep:02d}.json"


class ReplayRunner(AgentRunner):
    """Replays cassettes from a directory."""

    name = "replay"

    def __init__(self, cassette_dir: Path, *, strict: bool = True) -> None:
        self.cassette_dir = Path(cassette_dir)
        self.strict = strict

    def record(self, request: AgentRunRequest, result: AgentRunResult, patch: str) -> Path:
        """Write a cassette for a run that has just happened."""
        self.cassette_dir.mkdir(parents=True, exist_ok=True)
        path = self.cassette_dir / cassette_name(request.task_id, request.harness_id, request.rep)
        path.write_text(json.dumps({
            "task_id": request.task_id,
            "harness_id": request.harness_id,
            "rep": request.rep,
            "prompt": request.prompt,
            "result": result.model_dump(mode="json"),
            "patch": patch,
        }, indent=2))
        return path

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        path = self.cassette_dir / cassette_name(
            request.task_id, request.harness_id, request.rep
        )
        if not path.exists():
            if self.strict:
                raise CassetteNotFound(
                    f"no recording for task={request.task_id} harness={request.harness_id[:12]} "
                    f"rep={request.rep} (looked for {path})"
                )
            return AgentRunResult(
                adapter=self.name, exit_code=1, note="no cassette; treated as a failed run",
                usage=AgentUsage(source="unavailable"),
            )

        data = json.loads(path.read_text())
        patch = data.get("patch") or ""
        if patch.strip():
            proc = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=request.workspace, input=patch, capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise CassetteNotFound(
                    f"cassette {path.name} no longer applies to the fixture "
                    f"(the benchmark changed since it was recorded): {proc.stderr.strip()}"
                )

        result = AgentRunResult(**data["result"])
        result.adapter = f"replay({result.adapter})"
        result.note = (result.note + " | replayed recording").strip(" |")
        return result

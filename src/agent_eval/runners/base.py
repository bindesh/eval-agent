"""The agent runner interface.

One adapter drives one coding agent. The interface is deliberately narrow — give it a
prepared workspace and a prompt, get back what happened — so that adding Codex or Aider
later means writing one class, not touching the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

# "simulated" is a first-class value, not a variant of "estimated". A number produced
# by the offline demo agent must never be mistakable for one a provider reported.
UsageSource = Literal["actual", "estimated", "simulated", "unavailable"]


class AgentUsage(BaseModel):
    """Token and cost accounting, always labelled with where the number came from.

    ``source`` is the honest part. A missing number is reported as ``unavailable``; it is
    never back-filled with a guess, because a fabricated cost figure in a report about
    cost is worse than no figure at all.
    """

    source: UsageSource = "unavailable"
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_usd: float | None = None
    turns: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class AgentRunRequest(BaseModel):
    """Everything an adapter needs. The workspace is already prepared and harnessed."""

    run_id: str
    workspace: Path
    prompt: str
    model: str | None = None
    timeout_seconds: int = 600
    task_id: str = ""
    harness_id: str = ""
    arm: str = ""
    rep: int = 1
    seed: int = 0
    config: dict = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    """What the adapter observed. No grading happens here."""

    adapter: str
    adapter_version: str = "unknown"
    exit_code: int | None = None
    timed_out: bool = False
    duration_s: float = 0.0
    stdout: str = ""
    stderr: str = ""
    usage: AgentUsage = Field(default_factory=AgentUsage)
    model_reported: str | None = None
    session_id: str | None = None
    raw: dict | None = None
    simulated: bool = False
    note: str = ""


class AgentRunner(Protocol):
    """Anything that can attempt a task in a prepared workspace."""

    name: str

    def run(self, request: AgentRunRequest) -> AgentRunResult: ...

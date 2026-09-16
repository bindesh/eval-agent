"""Core domain models.

Everything the tool passes between stages is one of these. They are Pydantic models
so that anything written to an artifact file can be read back and validated, which is
what makes the later stages (judge, aggregate, decide, report) replayable on their own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .runners.base import AgentUsage

Dimension = Literal["correctness", "quality"]
CheckType = Literal["pytest", "command", "non_empty_diff"]


class CheckSpec(BaseModel):
    """A deterministic, objective check declared by a benchmark or a task.

    ``dimension`` decides which metric the result feeds. Keeping it on the spec (rather
    than inferring it from the check id) is what lets a benchmark add a project-specific
    check without the evaluator needing to know its name.
    """

    id: str
    type: CheckType
    dimension: Dimension = "correctness"
    args: list[str] = Field(default_factory=list)
    cmd: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    timeout_seconds: int = 300


class CheckOutcome(BaseModel):
    """The result of running one :class:`CheckSpec` against one workspace."""

    id: str
    type: CheckType
    dimension: Dimension
    passed: bool
    exit_code: int | None
    timed_out: bool = False
    duration_s: float
    command: list[str]
    stdout: str = ""
    stderr: str = ""

    @property
    def summary(self) -> str:
        if self.timed_out:
            return "TIMEOUT"
        return "PASS" if self.passed else "FAIL"


class TaskSpec(BaseModel):
    """One benchmark task: a prompt, a way to verify it, and a rubric to judge it."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    # Named `measures` on the model because `property` would shadow the builtin used by
    # the computed attributes below; the YAML key stays `property`, which reads better.
    measures: str = Field("", alias="property")
    prompt: str
    rubric: str = ""
    checks: list[CheckSpec]
    protected_paths: list[str] = Field(default_factory=list)
    timeout_seconds: int = 600
    directory: Path
    has_reference: bool = False

    @property
    def verify_dir(self) -> Path:
        return self.directory / "verify"

    @property
    def reference_dir(self) -> Path:
        return self.directory / "reference"


class BenchmarkSpec(BaseModel):
    """A fixture repository plus the tasks defined against it."""

    id: str
    title: str
    description: str = ""
    language: str = "python"
    directory: Path
    fixture_dir: Path
    tasks: list[TaskSpec]
    # Gitignore-style patterns for this benchmark's toolchain (e.g. Rust's `target/`).
    # Applied on top of the built-in cache excludes in workspace.py, never instead of them.
    workspace_excludes: list[str] = Field(default_factory=list)

    def task(self, task_id: str) -> TaskSpec:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"no such task: {task_id}")


class TamperInfo(BaseModel):
    """Whether the agent modified files the task declared off-limits.

    Tampering is neither ignored nor treated as an automatic failure: it is recorded,
    the protected files are restored before verification, and the run is routed to the
    manual-review queue. An agent that edits the test suite to make it pass is a real
    observed failure mode, and the report must say so rather than quietly scoring it.
    """

    detected: bool = False
    paths: list[str] = Field(default_factory=list)


class JudgeSummary(BaseModel):
    """The judged signal, carried on the run so every later stage sees its provenance.

    The model id and prompt version travel with the score because results from different
    judge prompts or different judge models are not comparable and must never be pooled.
    """

    score: float | None = None
    criteria: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = None
    low_agreement: bool = False
    model: str = ""
    provider: str = ""
    prompt_version: str = ""
    error: str = ""


class DiffStat(BaseModel):
    """Mechanical size of the patch. Context for a reader, never a quality score."""

    files: int = 0
    insertions: int = 0
    deletions: int = 0


class RunRecord(BaseModel):
    """Everything known about one (task x harness x repetition).

    This is the unit that gets written to disk, and every later stage reads it back from
    there rather than from memory — which is what makes judging, aggregation, the
    decision and the report re-runnable without spending another agent run.
    """

    run_id: str
    evaluation_id: str
    task_id: str
    arm: str
    rep: int
    order_index: int

    harness_id: str
    harness_short_id: str
    model: str | None = None
    model_reported: str | None = None
    agent_adapter: str = ""
    agent_adapter_version: str = "unknown"
    tool_version: str = ""
    simulated: bool = False

    prompt_sha256: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_s: float = 0.0
    exit_code: int | None = None
    timed_out: bool = False
    note: str = ""
    # An invalid run is one the agent never actually attempted (expired credentials, an
    # API outage). It is excluded from the metrics rather than counted as a failure:
    # counting it would turn an infrastructure problem into evidence about the harness.
    invalid: bool = False
    invalid_reason: str = ""

    base_commit: str = ""
    changed_files: list[str] = Field(default_factory=list)
    diff_stat: DiffStat = Field(default_factory=DiffStat)
    tamper: TamperInfo = Field(default_factory=TamperInfo)

    usage: AgentUsage = Field(default_factory=AgentUsage)
    checks: list[CheckOutcome] = Field(default_factory=list)
    judge: JudgeSummary | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)

    def check(self, check_id: str) -> CheckOutcome | None:
        for outcome in self.checks:
            if outcome.id == check_id:
                return outcome
        return None

    @property
    def correctness_passed(self) -> bool:
        """A run is functionally correct only if every correctness check passed.

        `all()` over an empty list is True, so the empty case is guarded: a run with no
        correctness checks is not silently counted as a success.
        """
        if self.invalid:
            return False
        checks = [c for c in self.checks if c.dimension == "correctness"]
        return bool(checks) and all(c.passed for c in checks)

    @property
    def quality_passed(self) -> bool:
        checks = [c for c in self.checks if c.dimension == "quality"]
        return bool(checks) and all(c.passed for c in checks)

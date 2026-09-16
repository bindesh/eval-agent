"""Benchmark validation — the "golden check".

Two failure modes make an evaluation quietly worthless, and neither is visible in the
final report:

* a **vacuous task**, whose verification already passes on the untouched fixture. Both
  harnesses score 1.0 on it, and it drags every measured difference toward zero.
* an **unsolvable task**, whose verification cannot pass even with a correct patch
  (a typo in the test, an import that does not resolve). Both harnesses score 0.0, with
  the same diluting effect.

`doctor` rules both out before any agent is run, by asserting that verification FAILS on
the pristine fixture and PASSES on a stored reference solution.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .checks import run_checks
from .models import BenchmarkSpec, CheckOutcome, TaskSpec
from .workspace import create_workspace


@dataclass
class TaskDiagnosis:
    """What `doctor` learned about one task."""

    task_id: str
    pristine: list[CheckOutcome] = field(default_factory=list)
    reference: list[CheckOutcome] = field(default_factory=list)
    reference_available: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def correctness_fails_on_pristine(self) -> bool:
        checks = [c for c in self.pristine if c.dimension == "correctness"]
        return bool(checks) and any(not c.passed for c in checks)

    @property
    def quality_clean_on_pristine(self) -> bool:
        return all(c.passed for c in self.pristine if c.dimension == "quality")

    @property
    def reference_passes(self) -> bool:
        return bool(self.reference) and all(c.passed for c in self.reference)

    @property
    def ok(self) -> bool:
        if not self.correctness_fails_on_pristine or not self.quality_clean_on_pristine:
            return False
        return self.reference_passes if self.reference_available else False

    def problems(self) -> list[str]:
        out: list[str] = []
        if not self.correctness_fails_on_pristine:
            out.append(
                "VACUOUS: verification already passes on the untouched fixture, so this "
                "task cannot distinguish two harnesses"
            )
        if not self.quality_clean_on_pristine:
            failing = [c.id for c in self.pristine if c.dimension == "quality" and not c.passed]
            out.append(
                f"DIRTY BASELINE: quality checks {failing} already fail on the untouched "
                "fixture, so they cannot attribute a failure to the agent"
            )
        if not self.reference_available:
            out.append("NO REFERENCE: cannot prove the task is solvable")
        elif not self.reference_passes:
            failing = [c.id for c in self.reference if not c.passed]
            out.append(f"UNSOLVABLE: reference solution does not pass checks {failing}")
        return out


def diagnose_task(
    benchmark: BenchmarkSpec, task: TaskSpec, *, workdir: Path, python: str | None = None
) -> TaskDiagnosis:
    """Run the golden check for one task."""
    diagnosis = TaskDiagnosis(task_id=task.id, reference_available=task.has_reference)

    pristine = create_workspace(
        benchmark.fixture_dir, workdir / f"{task.id}-pristine",
        extra_excludes=benchmark.workspace_excludes,
    )
    pristine.overlay(task.verify_dir, into="verify")
    diagnosis.pristine = run_checks(task.checks, pristine, python=python)

    if task.has_reference:
        solved = create_workspace(
            benchmark.fixture_dir, workdir / f"{task.id}-reference",
            extra_excludes=benchmark.workspace_excludes,
        )
        solved.overlay(task.reference_dir)
        solved.overlay(task.verify_dir, into="verify")
        diagnosis.reference = run_checks(task.checks, solved, python=python)

    return diagnosis


def diagnose(
    benchmark: BenchmarkSpec, *, task_ids: list[str] | None = None, python: str | None = None
) -> list[TaskDiagnosis]:
    """Run the golden check for every task (or the subset named by ``task_ids``)."""
    tasks = benchmark.tasks
    if task_ids:
        tasks = [benchmark.task(tid) for tid in task_ids]
    python = python or sys.executable
    results: list[TaskDiagnosis] = []
    with tempfile.TemporaryDirectory(prefix="agent-eval-doctor-") as tmp:
        for task in tasks:
            results.append(diagnose_task(benchmark, task, workdir=Path(tmp), python=python))
    return results

"""Planning and executing runs.

The order in which runs happen is part of the experiment design, not an implementation
detail — see :func:`build_plan`.
"""

from __future__ import annotations

import hashlib
import random
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .checks import run_checks
from .harness import HarnessSnapshot, overlay_files
from .models import BenchmarkSpec, DiffStat, RunRecord, TamperInfo, TaskSpec
from .runners.base import AgentRunner, AgentRunRequest
from .store import EvaluationStore
from .workspace import create_workspace

ARMS = ("baseline", "candidate")
DIFFSTAT_RE = re.compile(r"^@@|^\+\+\+|^---")


@dataclass(frozen=True)
class PlanItem:
    """One scheduled run."""

    task_id: str
    arm: str
    rep: int
    order_index: int

    def as_dict(self) -> dict:
        return {"task_id": self.task_id, "arm": self.arm, "rep": self.rep,
                "order_index": self.order_index}


def build_plan(
    task_ids: list[str], reps: int, *, order: str = "interleaved", seed: int = 0
) -> list[PlanItem]:
    """Decide the execution order.

    ``interleaved`` (the default) runs baseline and candidate back-to-back for each
    repetition of each task. This matters more than it looks: API latency, model routing
    and machine load drift over a forty-minute evaluation, so running all the baseline
    runs first and all the candidate runs afterwards would confound "which harness" with
    "what time it was". Interleaving makes that drift hit both arms equally.

    ``blocked`` is the naive order, kept so the difference can be demonstrated.
    ``random`` shuffles under the evaluation seed, which is the most defensible choice at
    larger sample sizes but adds nothing at n=5 tasks.
    """
    pairs: list[tuple[str, str, int]] = []
    if order == "blocked":
        for arm in ARMS:
            for task_id in task_ids:
                for rep in range(1, reps + 1):
                    pairs.append((task_id, arm, rep))
    else:
        for task_id in task_ids:
            for rep in range(1, reps + 1):
                for arm in ARMS:
                    pairs.append((task_id, arm, rep))
        if order == "random":
            random.Random(seed).shuffle(pairs)

    return [PlanItem(t, a, r, i) for i, (t, a, r) in enumerate(pairs)]


def _diff_stat(diff: str) -> DiffStat:
    files = sum(1 for line in diff.splitlines() if line.startswith("+++ "))
    insertions = sum(
        1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
    )
    return DiffStat(files=files, insertions=insertions, deletions=deletions)


def execute_run(
    *,
    benchmark: BenchmarkSpec,
    task: TaskSpec,
    harness: HarnessSnapshot,
    item: PlanItem,
    runner: AgentRunner,
    store: EvaluationStore,
    model: str | None,
    timeout_seconds: int | None = None,
    seed: int = 0,
    keep_workspace: bool = False,
    python: str | None = None,
) -> RunRecord:
    """Run one task once under one harness, verify it, and persist the evidence.

    The ordering below is the load-bearing part:

    1. workspace is created and committed **before** the harness is overlaid, so the
       harness files are not part of the base tree;
    2. the harness is overlaid with git exclusion, so it never appears in the patch the
       agent is graded on and never reaches the judge;
    3. the patch is captured **before** anything of ours is copied in, so the verification
       suite cannot contaminate it;
    4. protected paths are restored **before** the checks run, so an agent that edited the
       existing tests cannot benefit from it;
    5. only then is ``verify/`` copied in and the checks run.
    """
    run_id = f"{item.task_id}-{item.arm}-rep{item.rep:02d}"
    workspace_root = Path(tempfile.mkdtemp(prefix=f"agent-eval-{run_id}-"))
    workspace = create_workspace(
        benchmark.fixture_dir, workspace_root / "repo",
        extra_excludes=benchmark.workspace_excludes,
    )

    # (2) harness overlay, hidden from git
    workspace.overlay(harness.source, exclude_from_git=True)
    for name in ("harness.yaml",):
        stray = workspace.path / name
        if stray.exists():
            stray.unlink()  # agent-eval's own config is not part of the agent's repo
    overlaid = overlay_files(harness)

    # The harness owns its own agent configuration. `harness.yaml` declares the model,
    # the permission mode and any extra CLI flags, and all three are part of the harness
    # content hash - so a change to any of them IS a harness change and shows in the diff.
    # They must therefore actually reach the adapter: a harness whose permission mode
    # changed would otherwise be reported as changed and then run as though it had not.
    # The evaluation-level `--model` is a default for harnesses that do not name one.
    effective_model = harness.model or model

    started = datetime.now(UTC)
    request = AgentRunRequest(
        run_id=run_id, workspace=workspace.path, prompt=task.prompt, model=effective_model,
        timeout_seconds=timeout_seconds or task.timeout_seconds,
        task_id=task.id, harness_id=harness.harness_id, arm=item.arm, rep=item.rep, seed=seed,
        config=harness.config,
    )
    result = runner.run(request)
    ended = datetime.now(UTC)

    # (3) capture the patch before our own files land in the workspace
    diff = workspace.diff()
    changed = workspace.changed_files()

    # (4) restore protected paths; anything that had to be restored is tampering
    tampered = workspace.restore(task.protected_paths)

    # (5) hidden verification, then the objective checks
    workspace.overlay(task.verify_dir, into="verify", exclude_from_git=True)
    outcomes = run_checks(task.checks, workspace, python=python)

    record = RunRecord(
        run_id=run_id, evaluation_id=store.evaluation_id, task_id=task.id, arm=item.arm,
        rep=item.rep, order_index=item.order_index,
        harness_id=harness.harness_id, harness_short_id=harness.short_id,
        model=effective_model, model_reported=result.model_reported,
        agent_adapter=result.adapter, agent_adapter_version=result.adapter_version,
        tool_version=__version__, simulated=result.simulated,
        prompt_sha256=hashlib.sha256(task.prompt.encode()).hexdigest(),
        started_at=started.isoformat(), ended_at=ended.isoformat(),
        duration_s=result.duration_s, exit_code=result.exit_code, timed_out=result.timed_out,
        note=result.note, base_commit=workspace.base_commit,
        invalid=bool(result.infrastructure_error),
        invalid_reason=result.infrastructure_error,
        changed_files=[f for f in changed if f not in overlaid],
        diff_stat=_diff_stat(diff),
        tamper=TamperInfo(detected=bool(tampered), paths=tampered),
        usage=result.usage, checks=outcomes,
    )
    record = store.write_run(
        record, stdout=result.stdout, stderr=result.stderr, diff=diff, raw=result.raw
    )

    if keep_workspace:
        record.artifacts["workspace"] = str(workspace.path)
    else:
        shutil.rmtree(workspace_root, ignore_errors=True)
    return record

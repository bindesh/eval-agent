"""Aggregating runs into metrics, and comparing two arms.

Aggregation always goes ``run -> task -> arm``. Never ``run -> arm``: see ``stats`` for
why the intermediate step is the whole ballgame.

The five dimensions are kept separate all the way through. Nothing here produces a
combined score, because combining them requires an exchange rate between "one more task
passes" and "twelve cents a run" that nobody can defend.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .models import RunRecord
from .stats import (
    Interval,
    mean,
    median,
    paired_bootstrap_ci,
    percentile,
    relative_change,
    wilson_interval,
)


class TaskArmMetrics(BaseModel):
    """Everything measured for one task under one harness, across its repetitions."""

    task_id: str
    arm: str
    reps: int = 0
    passes: int = 0
    pass_rate: float = 0.0
    quality_rate: float = 0.0
    consistency: float = 1.0
    median_duration_s: float = 0.0
    p90_duration_s: float = 0.0
    median_cost_usd: float | None = None
    median_tokens: float | None = None
    cost_source: str = "unavailable"
    tamper_runs: int = 0
    timeouts: int = 0
    invalid_runs: int = 0
    judge_score: float | None = None
    judge_criteria: dict[str, float] = Field(default_factory=dict)
    judge_low_agreement: bool = False
    run_ids: list[str] = Field(default_factory=list)
    artifact_dirs: list[str] = Field(default_factory=list)

    @property
    def modal_pass(self) -> bool:
        """The outcome a majority of repetitions produced. Ties (e.g. 1/2) count as fail —
        a task that only works half the time is not a task the harness can be said to pass."""
        return self.pass_rate > 0.5

    @property
    def flaky(self) -> bool:
        return 0.0 < self.pass_rate < 1.0


class ArmSummary(BaseModel):
    """One harness, summarised across tasks."""

    arm: str
    harness_short_id: str = ""
    n_tasks: int = 0
    n_runs: int = 0
    pooled_passes: int = 0
    pooled_runs: int = 0
    pooled_pass_rate: float = 0.0
    pooled_ci_low: float = 0.0
    pooled_ci_high: float = 1.0
    mean_task_pass_rate: float = 0.0
    mean_quality_rate: float = 0.0
    mean_consistency: float = 1.0
    flaky_tasks: int = 0
    invalid_runs: int = 0
    median_duration_s: float = 0.0
    p90_duration_s: float = 0.0
    mean_cost_per_task: float | None = None
    total_cost_usd: float | None = None
    cost_source: str = "unavailable"
    tamper_runs: int = 0
    timeouts: int = 0
    judge_mean: float | None = None
    tasks: list[TaskArmMetrics] = Field(default_factory=list)


class MetricDelta(BaseModel):
    """A paired difference between arms, with its uncertainty and its direction."""

    metric: str
    label: str
    unit: str = ""
    higher_is_better: bool = True
    objective: bool = True
    baseline: float | None = None
    candidate: float | None = None
    absolute: float | None = None
    relative: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    note: str = ""

    @property
    def significant(self) -> bool:
        """Whether the interval clears zero. Not 'statistically significant' in the
        hypothesis-testing sense — just: does the evidence exclude no-change?"""
        if self.ci_low is None or self.ci_high is None:
            return False
        return self.ci_low > 0 or self.ci_high < 0

    @property
    def improved(self) -> bool:
        if self.absolute is None:
            return False
        return self.absolute > 0 if self.higher_is_better else self.absolute < 0


class TaskComparison(BaseModel):
    """Per-task, side by side. This is where regressions become visible."""

    task_id: str
    title: str = ""
    baseline: TaskArmMetrics
    candidate: TaskArmMetrics
    classification: str = "unchanged"
    correctness_delta: float = 0.0
    cost_delta: float | None = None
    duration_delta: float = 0.0
    judge_delta: float | None = None
    flags: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    """Something a human should look at, with the evidence attached."""

    kind: str
    severity: str  # critical | warning | info
    task_id: str = ""
    message: str
    evidence: list[str] = Field(default_factory=list)


class Comparison(BaseModel):
    """The full comparison. Consumed by the decision stage and the report."""

    baseline: ArmSummary
    candidate: ArmSummary
    tasks: list[TaskComparison] = Field(default_factory=list)
    deltas: dict[str, MetricDelta] = Field(default_factory=dict)
    regressions: list[Finding] = Field(default_factory=list)
    improvements: list[Finding] = Field(default_factory=list)
    manual_review: list[Finding] = Field(default_factory=list)
    n_tasks: int = 0
    n_reps: int = 0
    judge_high_threshold: float = 4.0
    judge_low_threshold: float = 3.0


# ---------------------------------------------------------------------------


def _cost_source(records: list[RunRecord]) -> str:
    sources = {r.usage.source for r in records}
    for preferred in ("actual", "simulated", "estimated"):
        if preferred in sources:
            return preferred
    return "unavailable"


def task_arm_metrics(task_id: str, arm: str, records: list[RunRecord]) -> TaskArmMetrics:
    """Collapse the repetitions of one (task, arm) into one row."""
    if not records:
        return TaskArmMetrics(task_id=task_id, arm=arm)

    invalid = [r for r in records if r.invalid]
    # Runs the agent never attempted are removed from the denominator, not scored as
    # failures. They are counted separately and block the verdict in gate 0, so the
    # sample never shrinks silently.
    records = [r for r in records if not r.invalid]
    if not records:
        return TaskArmMetrics(task_id=task_id, arm=arm, invalid_runs=len(invalid))

    passes = sum(1 for r in records if r.correctness_passed)
    pass_rate = passes / len(records)
    durations = [r.duration_s for r in records]
    costs = [r.usage.cost_usd for r in records if r.usage.cost_usd is not None]
    tokens = [r.usage.total_tokens for r in records if r.usage.total_tokens is not None]

    judge_scores = [r.judge.score for r in records if r.judge and r.judge.score is not None]
    criteria: dict[str, list[float]] = {}
    for record in records:
        for key, value in ((record.judge.criteria if record.judge else None) or {}).items():
            criteria.setdefault(key, []).append(float(value))

    return TaskArmMetrics(
        task_id=task_id, arm=arm, reps=len(records), passes=passes, pass_rate=pass_rate,
        quality_rate=sum(1 for r in records if r.quality_passed) / len(records),
        # Consistency is how far the task is from a coin flip: 1.0 when every repetition
        # agreed, 0.5 when they split evenly. It measures repeatability, not correctness.
        consistency=max(pass_rate, 1 - pass_rate),
        median_duration_s=median(durations), p90_duration_s=percentile(durations, 0.9),
        median_cost_usd=median(costs) if costs else None,
        median_tokens=median([float(value) for value in tokens]) if tokens else None,
        cost_source=_cost_source(records),
        invalid_runs=len(invalid),
        tamper_runs=sum(1 for r in records if r.tamper.detected),
        timeouts=sum(1 for r in records if r.timed_out),
        judge_score=mean([s for s in judge_scores if s is not None]) if judge_scores else None,
        judge_criteria={k: mean(v) for k, v in criteria.items()},
        judge_low_agreement=any(r.judge.low_agreement for r in records if r.judge),
        run_ids=[r.run_id for r in records],
        artifact_dirs=[r.artifacts.get("dir", "") for r in records],
    )


def summarise_arm(
    arm: str, task_metrics: list[TaskArmMetrics], harness_short_id: str = ""
) -> ArmSummary:
    """Roll tasks up into one arm. Note `mean_task_pass_rate` weights every task equally,
    regardless of how many repetitions it happened to get."""
    pooled_runs = sum(t.reps for t in task_metrics)
    pooled_passes = sum(t.passes for t in task_metrics)
    interval = wilson_interval(pooled_passes, pooled_runs)
    costs = [t.median_cost_usd for t in task_metrics if t.median_cost_usd is not None]
    judged = [t.judge_score for t in task_metrics if t.judge_score is not None]

    return ArmSummary(
        arm=arm, harness_short_id=harness_short_id,
        n_tasks=len(task_metrics), n_runs=pooled_runs,
        pooled_passes=pooled_passes, pooled_runs=pooled_runs,
        pooled_pass_rate=pooled_passes / pooled_runs if pooled_runs else 0.0,
        pooled_ci_low=interval.low, pooled_ci_high=interval.high,
        mean_task_pass_rate=mean([t.pass_rate for t in task_metrics]),
        mean_quality_rate=mean([t.quality_rate for t in task_metrics]),
        mean_consistency=mean([t.consistency for t in task_metrics]),
        flaky_tasks=sum(1 for t in task_metrics if t.flaky),
        invalid_runs=sum(t.invalid_runs for t in task_metrics),
        median_duration_s=median([t.median_duration_s for t in task_metrics]),
        p90_duration_s=percentile([t.p90_duration_s for t in task_metrics], 0.9),
        mean_cost_per_task=mean(costs) if costs else None,
        total_cost_usd=sum(costs) if costs else None,
        cost_source=task_metrics[0].cost_source if task_metrics else "unavailable",
        tamper_runs=sum(t.tamper_runs for t in task_metrics),
        timeouts=sum(t.timeouts for t in task_metrics),
        judge_mean=mean(judged) if judged else None,
        tasks=task_metrics,
    )


def _paired(
    baseline: list[TaskArmMetrics],
    candidate: list[TaskArmMetrics],
    attribute: str,
) -> tuple[list[float], list[str]]:
    """Per-task differences, keeping only tasks present in both arms (that is the pairing)."""
    by_task = {t.task_id: t for t in baseline}
    deltas: list[float] = []
    task_ids: list[str] = []
    for task in candidate:
        other = by_task.get(task.task_id)
        if other is None:
            continue
        left, right = getattr(task, attribute), getattr(other, attribute)
        if left is None or right is None:
            continue
        deltas.append(float(left) - float(right))
        task_ids.append(task.task_id)
    return deltas, task_ids


def _delta(
    metric: str, label: str, baseline: ArmSummary, candidate: ArmSummary, attribute: str,
    *, summary_attribute: str, unit: str = "", higher_is_better: bool = True,
    objective: bool = True, bootstrap: bool = True, samples: int = 10_000,
    confidence: float = 0.95, seed: int = 0,
) -> MetricDelta:
    deltas, _ = _paired(baseline.tasks, candidate.tasks, attribute)
    base_value = getattr(baseline, summary_attribute)
    cand_value = getattr(candidate, summary_attribute)

    if not deltas:
        return MetricDelta(
            metric=metric, label=label, unit=unit, higher_is_better=higher_is_better,
            objective=objective, baseline=base_value, candidate=cand_value,
            note="not measured on any task in both arms",
        )

    interval: Interval | None = None
    if bootstrap:
        interval = paired_bootstrap_ci(
            deltas, samples=samples, confidence=confidence, seed=seed
        )
    absolute = mean(deltas)
    return MetricDelta(
        metric=metric, label=label, unit=unit, higher_is_better=higher_is_better,
        objective=objective, baseline=base_value, candidate=cand_value,
        absolute=absolute, relative=relative_change(base_value, cand_value),
        ci_low=interval.low if interval else None,
        ci_high=interval.high if interval else None,
    )


def _classify(base: TaskArmMetrics, cand: TaskArmMetrics) -> str:
    """Per-task verdict on the modal outcome.

    `regressed` uses the modal outcome rather than the mean, because "it used to work and
    now it usually doesn't" is the thing a harness owner must not ship, and averaging
    hides it behind the other four tasks.
    """
    if base.modal_pass and not cand.modal_pass:
        return "regressed"
    if not base.modal_pass and cand.modal_pass:
        return "improved"
    if base.modal_pass and cand.modal_pass:
        return "both_pass"
    return "both_fail"


def compare(
    baseline: ArmSummary, candidate: ArmSummary, *, titles: dict[str, str] | None = None,
    bootstrap_samples: int = 10_000, confidence: float = 0.95, seed: int = 0,
    judge_enabled: bool = False, judge_high_threshold: float = 4.0,
    judge_low_threshold: float = 3.0,
) -> Comparison:
    """Build the full paired comparison, including regressions and the review queue."""
    titles = titles or {}
    kwargs: dict[str, Any] = {
        "samples": bootstrap_samples, "confidence": confidence, "seed": seed
    }

    deltas = {
        "correctness": _delta(
            "correctness", "Functional correctness", baseline, candidate, "pass_rate",
            summary_attribute="mean_task_pass_rate", unit="pass rate", **kwargs),
        "quality": _delta(
            "quality", "Objective quality (lint, types)", baseline, candidate, "quality_rate",
            summary_attribute="mean_quality_rate", unit="pass rate", **kwargs),
        "reliability": _delta(
            "reliability", "Reliability (agreement across repetitions)", baseline, candidate,
            "consistency", summary_attribute="mean_consistency", unit="consistency", **kwargs),
        "duration": _delta(
            "duration", "Wall-clock per task", baseline, candidate, "median_duration_s",
            summary_attribute="median_duration_s", unit="s", higher_is_better=False, **kwargs),
        "cost": _delta(
            "cost", "Cost per task", baseline, candidate, "median_cost_usd",
            summary_attribute="mean_cost_per_task", unit="USD", higher_is_better=False, **kwargs),
    }
    if judge_enabled:
        deltas["judge"] = _delta(
            "judge", "Requirement adherence (judged)", baseline, candidate, "judge_score",
            summary_attribute="judge_mean", unit="1-5", objective=False, **kwargs)

    by_task = {t.task_id: t for t in baseline.tasks}
    comparisons: list[TaskComparison] = []
    regressions: list[Finding] = []
    improvements: list[Finding] = []
    manual_review: list[Finding] = []

    for cand in candidate.tasks:
        base = by_task.get(cand.task_id)
        if base is None:
            continue
        classification = _classify(base, cand)
        flags: list[str] = []

        if cand.tamper_runs or base.tamper_runs:
            flags.append("tamper")
            manual_review.append(Finding(
                kind="tamper", severity="critical", task_id=cand.task_id,
                message=(f"{cand.task_id}: protected files were modified and restored in "
                         f"{base.tamper_runs + cand.tamper_runs} run(s); the agent may have "
                         f"tried to change the verification rather than satisfy it"),
                evidence=base.artifact_dirs + cand.artifact_dirs,
            ))
        if cand.judge_low_agreement or base.judge_low_agreement:
            flags.append("judge-disagrees-with-itself")
            manual_review.append(Finding(
                kind="judge_low_agreement", severity="warning", task_id=cand.task_id,
                message=(f"{cand.task_id}: the judge scored the same patch inconsistently; "
                         f"the affected criteria are reported but excluded from the "
                         f"aggregate"),
                evidence=cand.artifact_dirs,
            ))
        # The most valuable cell in the whole tool: the objective and the judged signal
        # point in opposite directions, so at least one of them is wrong.
        if cand.judge_score is not None:
            if cand.modal_pass and cand.judge_score <= judge_low_threshold:
                flags.append("passes-tests-but-judged-poor")
                manual_review.append(Finding(
                    kind="disagreement", severity="warning", task_id=cand.task_id,
                    message=(f"{cand.task_id}: candidate passes the tests but the judge "
                             f"scored it {cand.judge_score:.1f}/5 - either the patch satisfies "
                             f"the letter of the tests without meeting the requirement, or "
                             f"the judge is wrong"),
                    evidence=cand.artifact_dirs,
                ))
            elif not cand.modal_pass and cand.judge_score >= judge_high_threshold:
                flags.append("judged-good-but-fails-tests")
                manual_review.append(Finding(
                    kind="disagreement", severity="warning", task_id=cand.task_id,
                    message=(f"{cand.task_id}: candidate fails the tests but the judge "
                             f"scored it {cand.judge_score:.1f}/5 - suspect the benchmark "
                             f"before the agent"),
                    evidence=cand.artifact_dirs,
                ))

        if classification == "regressed":
            regressions.append(Finding(
                kind="correctness", severity="critical", task_id=cand.task_id,
                message=(f"{cand.task_id}: baseline passed {base.passes}/{base.reps} "
                         f"repetitions, candidate passed {cand.passes}/{cand.reps}"),
                evidence=cand.artifact_dirs,
            ))
        elif classification == "improved":
            improvements.append(Finding(
                kind="correctness", severity="info", task_id=cand.task_id,
                message=(f"{cand.task_id}: baseline passed {base.passes}/{base.reps} "
                         f"repetitions, candidate passed {cand.passes}/{cand.reps}"),
                evidence=cand.artifact_dirs,
            ))
        if cand.flaky and not base.flaky:
            flags.append("became-flaky")
            regressions.append(Finding(
                kind="reliability", severity="warning", task_id=cand.task_id,
                message=(f"{cand.task_id}: candidate is inconsistent "
                         f"({cand.passes}/{cand.reps}) where baseline was not"),
                evidence=cand.artifact_dirs,
            ))

        comparisons.append(TaskComparison(
            task_id=cand.task_id, title=titles.get(cand.task_id, ""),
            baseline=base, candidate=cand, classification=classification,
            correctness_delta=cand.pass_rate - base.pass_rate,
            cost_delta=(cand.median_cost_usd - base.median_cost_usd)
            if cand.median_cost_usd is not None and base.median_cost_usd is not None else None,
            duration_delta=cand.median_duration_s - base.median_duration_s,
            judge_delta=(cand.judge_score - base.judge_score)
            if cand.judge_score is not None and base.judge_score is not None else None,
            flags=flags,
        ))

    reps = max((t.reps for t in candidate.tasks), default=0)
    return Comparison(
        baseline=baseline, candidate=candidate, tasks=comparisons, deltas=deltas,
        regressions=regressions, improvements=improvements, manual_review=manual_review,
        n_tasks=len(comparisons), n_reps=reps,
        judge_high_threshold=judge_high_threshold, judge_low_threshold=judge_low_threshold,
    )

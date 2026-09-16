"""Stage orchestration.

The stages after `run` are pure functions over stored artifacts, so they live here and are
shared by `evaluate` (which has just produced the runs) and `report` (which reads runs
someone else produced, possibly weeks ago). That sharing is the point: rebuilding a report
with a different threshold must execute exactly the same code as producing it the first
time, or the two will drift and the stored evidence stops meaning what the report says.
"""

from __future__ import annotations

from pathlib import Path

from .benchmark import load_benchmark
from .config import Config, DecisionSettings
from .decide import Decision, decide
from .harness import HarnessSnapshot, diff_harnesses, snapshot_harness
from .metrics import Comparison, compare, summarise_arm, task_arm_metrics
from .models import BenchmarkSpec, RunRecord
from .report import build_report, render_html
from .store import EvaluationStore


def aggregate(records: list[RunRecord], *, judge_enabled: bool = False,
              titles: dict[str, str] | None = None,
              settings: DecisionSettings | None = None) -> Comparison:
    """runs -> per (task, arm) -> per arm -> paired comparison."""
    settings = settings or DecisionSettings()
    arms: dict[str, list] = {}
    for arm in ("baseline", "candidate"):
        by_task: dict[str, list[RunRecord]] = {}
        for record in records:
            if record.arm == arm:
                by_task.setdefault(record.task_id, []).append(record)
        arms[arm] = [
            task_arm_metrics(task_id, arm, sorted(runs, key=lambda r: r.rep))
            for task_id, runs in sorted(by_task.items())
        ]

    short_ids = {
        arm: next((r.harness_short_id for r in records if r.arm == arm), "")
        for arm in ("baseline", "candidate")
    }
    baseline = summarise_arm("baseline", arms["baseline"], short_ids["baseline"])
    candidate = summarise_arm("candidate", arms["candidate"], short_ids["candidate"])
    return compare(
        baseline, candidate, titles=titles, judge_enabled=judge_enabled,
        bootstrap_samples=settings.bootstrap_samples, confidence=settings.confidence,
        judge_high_threshold=settings.judge_high_threshold,
        judge_low_threshold=settings.judge_low_threshold,
    )


def finalise(
    store: EvaluationStore, config: Config, *, benchmark: BenchmarkSpec | None = None,
    baseline: HarnessSnapshot | None = None, candidate: HarnessSnapshot | None = None,
) -> tuple[Comparison, Decision, dict, Path, Path]:
    """Aggregate, decide, and write both reports. Safe to call repeatedly."""
    metadata = store.read_metadata()
    benchmark = benchmark or load_benchmark(config.evaluation.benchmark)
    baseline = baseline or snapshot_harness(config.evaluation.baseline)
    candidate = candidate or snapshot_harness(config.evaluation.candidate)

    records = store.read_runs()
    judged = any(r.judge and r.judge.score is not None for r in records)
    titles = {t.id: t.title for t in benchmark.tasks}

    comparison = aggregate(
        records, judge_enabled=judged, titles=titles, settings=config.decision
    )
    harness_diff = diff_harnesses(baseline, candidate)
    decision = decide(comparison, harness_diff, config.decision)

    report = build_report(
        comparison=comparison, decision=decision, harness_diff=harness_diff,
        baseline=baseline, candidate=candidate, benchmark=benchmark, records=records,
        metadata=metadata, evaluation_id=store.evaluation_id,
    )
    store.write_json("summary.json", {
        "verdict": decision.model_dump(mode="json"),
        "arms": {"baseline": comparison.baseline.model_dump(mode="json"),
                 "candidate": comparison.candidate.model_dump(mode="json")},
        "deltas": {k: v.model_dump(mode="json") for k, v in comparison.deltas.items()},
    })
    json_path = store.write_json("report.json", report)
    html_path = store.write_text("report.html", render_html(report))
    return comparison, decision, report, json_path, html_path

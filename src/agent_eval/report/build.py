"""Building the evaluation report.

Two outputs from one model: `report.json` (machine-readable, every number the HTML shows)
and `report.html` (the reviewer experience, a single self-contained file).

The report's job is not to be convincing. It is to let a reader disagree with it: every
number is next to the artifact path that produced it, every judged number is visually
separated from every measured one, and the limitations are a section rather than a
footnote.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import __version__
from ..decide import Decision
from ..harness import HarnessDiff, HarnessSnapshot
from ..metrics import Comparison
from ..models import BenchmarkSpec, RunRecord
from ..stats import resolvable_effect

SCHEMA_VERSION = 1
TEMPLATE_DIR = Path(__file__).parent / "templates"


def disagreement_matrix(comparison: Comparison) -> dict:
    """The 2x2 of measured correctness against judged adherence, for the candidate arm.

    The diagonal is agreement. The off-diagonal cells are the most valuable output of the
    tool: they are exactly where the agent, the benchmark, or the judge is wrong, and a
    human has to decide which.
    """
    high_threshold = comparison.judge_high_threshold
    low_threshold = comparison.judge_low_threshold
    cells: dict[str, list[str]] = {
        "pass_high": [], "pass_mid": [], "pass_low": [],
        "fail_high": [], "fail_mid": [], "fail_low": [],
    }
    for task in comparison.tasks:
        score = task.candidate.judge_score
        if score is None:
            continue
        row = "pass" if task.candidate.modal_pass else "fail"
        if score >= high_threshold:
            band = "high"
        elif score <= low_threshold:
            band = "low"
        else:
            # The middle band exists because an earlier version had none: every score
            # below "high" landed in the alarming cell, so the matrix flagged four tasks
            # of five while the review queue - which used a different threshold - listed
            # two. A report that contradicts itself is worse than no report.
            band = "mid"
        cells[f"{row}_{band}"].append(task.task_id)
    return {
        "available": any(cells.values()),
        "cells": cells,
        "thresholds": {"high": high_threshold, "low": low_threshold},
        "legend": {
            "pass_high": "agreement - measured and judged signals both positive",
            "pass_mid": "no strong judged signal either way",
            "pass_low": "passes the tests but the judge found the requirement unmet - "
                        "suspect a patch that satisfies the letter of the tests",
            "fail_high": "judged good but fails the tests - suspect the benchmark, "
                         "not the agent",
            "fail_mid": "no strong judged signal either way",
            "fail_low": "agreement - measured and judged signals both negative",
        },
    }


def limitations(comparison: Comparison, decision: Decision, simulated: bool) -> list[str]:
    """Stated in the report itself, not only in the README."""
    detectable = decision.resolvable_effect
    items = [
        f"Sample size: {comparison.n_tasks} tasks x {comparison.n_reps} repetitions. The "
        f"unit of analysis is the task, not the run, so the effective n is "
        f"{comparison.n_tasks} - not {comparison.n_tasks * comparison.n_reps}.",
        (
            f"Resolving power: this design can only resolve paired differences of about "
            f"{detectable * 100:.0f} percentage points or more. Smaller real effects will "
            f"read as INCONCLUSIVE here."
            if detectable is not None else
            "Resolving power: this design cannot resolve any effect short of every task "
            "flipping. Add tasks or repetitions before drawing conclusions."
        ),
        "Benchmark validity: a harness tuned against these tasks will score well on these "
        "tasks. The tool cannot detect its own overfitting; rotate and hold out tasks.",
        "Judge reliability is measured by self-consistency only, never against human "
        "labels. A consistent judge can be consistently wrong.",
        "Checks run in the evaluator's Python environment; tasks cannot yet declare their "
        "own dependencies.",
        "Agents are not deterministic even at temperature 0. Everything that can be pinned "
        "is pinned and recorded; the variance that remains is measured, not hidden.",
    ]
    if simulated:
        items.insert(0, (
            "THIS EVALUATION USED THE SIMULATED AGENT. Which attempt each run produced, "
            "and every duration, token and cost figure, were declared by the benchmark "
            "rather than measured. The patches, the workspaces and every objective check "
            "are real. Do not cite these numbers as evidence about a real agent."
        ))
    return items


def build_report(
    *, comparison: Comparison, decision: Decision, harness_diff: HarnessDiff,
    baseline: HarnessSnapshot, candidate: HarnessSnapshot, benchmark: BenchmarkSpec,
    records: list[RunRecord], metadata: dict, evaluation_id: str,
) -> dict:
    """Assemble the machine-readable report."""
    simulated = any(r.simulated for r in records)
    config = metadata.get("config", {})
    evaluation_cfg = config.get("evaluation", {})
    agent_cfg = config.get("agent", {})
    titles = {t.id: t.title for t in benchmark.tasks}

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "tool_version": __version__,
        "evaluation": {
            "id": evaluation_id,
            "name": evaluation_cfg.get("name", ""),
            "benchmark": benchmark.id,
            "benchmark_title": benchmark.title,
            "n_tasks": comparison.n_tasks,
            "n_reps": comparison.n_reps,
            "n_runs": len(records),
            "order": evaluation_cfg.get("order", "interleaved"),
            "seed": evaluation_cfg.get("seed", 0),
            "agent_adapter": agent_cfg.get("adapter", ""),
            "model": agent_cfg.get("model"),
            "simulated": simulated,
            "judge_enabled": "judge" in comparison.deltas,
        },
        "environment": metadata.get("environment", {}),
        "harnesses": {
            "baseline": {
                "name": baseline.name, "id": baseline.harness_id,
                "short_id": baseline.short_id, "files": len(baseline.files),
                "git_commit": baseline.git_commit, "git_dirty": baseline.git_dirty,
                "model": baseline.model, "agent": baseline.agent,
            },
            "candidate": {
                "name": candidate.name, "id": candidate.harness_id,
                "short_id": candidate.short_id, "files": len(candidate.files),
                "git_commit": candidate.git_commit, "git_dirty": candidate.git_dirty,
                "model": candidate.model, "agent": candidate.agent,
            },
        },
        # Pydantic does not serialise properties, so the computed flags are added
        # explicitly. They are not cosmetic: the template renders "MODEL: unchanged" from
        # `model_changed`, and a missing key is falsy in Jinja - so omitting them made the
        # report claim the model was unchanged even when it had changed, which is the one
        # thing the comparability section exists to catch.
        "harness_diff": harness_diff.model_dump(mode="json") | {
            "counts": harness_diff.counts(),
            "model_changed": harness_diff.model_changed,
            "agent_changed": harness_diff.agent_changed,
            "identical": harness_diff.identical,
        },
        "verdict": decision.model_dump(mode="json"),
        "arms": {
            "baseline": comparison.baseline.model_dump(mode="json"),
            "candidate": comparison.candidate.model_dump(mode="json"),
        },
        "deltas": {k: v.model_dump(mode="json") | {"significant": v.significant}
                   for k, v in comparison.deltas.items()},
        "tasks": [
            t.model_dump(mode="json") | {"title": titles.get(t.task_id, "")}
            for t in comparison.tasks
        ],
        "regressions": [f.model_dump(mode="json") for f in comparison.regressions],
        "improvements": [f.model_dump(mode="json") for f in comparison.improvements],
        "manual_review": [f.model_dump(mode="json") for f in comparison.manual_review],
        "disagreement_matrix": disagreement_matrix(comparison),
        "limitations": limitations(comparison, decision, simulated),
        "runs": [
            {
                "run_id": r.run_id, "task_id": r.task_id, "arm": r.arm, "rep": r.rep,
                "order_index": r.order_index, "duration_s": r.duration_s,
                "correctness": r.correctness_passed, "quality": r.quality_passed,
                "tamper": r.tamper.detected, "cost_usd": r.usage.cost_usd,
                "cost_source": r.usage.source, "artifacts": r.artifacts.get("dir", ""),
                "checks": {c.id: c.summary for c in r.checks},
                "judge_score": r.judge.score if r.judge else None,
            }
            for r in records
        ],
        "power": {
            "resolvable_effect": resolvable_effect(comparison.n_tasks, comparison.n_reps),
            "n_tasks": comparison.n_tasks,
            "n_reps": comparison.n_reps,
        },
    }


def render_html(report: dict) -> str:
    """Render the single-file HTML report."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    env.filters["pct"] = lambda v: "n/a" if v is None else f"{v * 100:.1f}%"
    env.filters["pp"] = lambda v: "n/a" if v is None else f"{v * 100:+.1f}pp"
    env.filters["usd"] = lambda v: "n/a" if v is None else f"${v:.4f}"
    env.filters["secs"] = lambda v: "n/a" if v is None else f"{v:.1f}s"
    env.filters["signed"] = lambda v: "n/a" if v is None else f"{v:+.2f}"
    env.filters["relpct"] = lambda v: "n/a" if v is None else f"{v * 100:+.1f}%"
    return env.get_template("report.html.j2").render(**report)

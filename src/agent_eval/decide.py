"""The decision rules.

There is no weighted composite score here, and that is the single most important design
decision in the tool.

A composite — ``0.5*correctness + 0.3*quality + 0.2*cost`` — requires an exchange rate
between "one more task passes" and "twelve cents a run". No such rate exists, so inventing
one buries a value judgement inside a constant and makes the verdict unarguable: a reader
who disagrees has nowhere to put their objection.

Instead this is the structure a real product experiment uses: **one pre-declared primary
metric plus guardrails**, evaluated as ordered gates. The value judgements become named,
configurable tolerances that a reader can see and disagree with. And a regression on one
critical task can veto an average improvement — which a weighted sum can never do,
because it can always be outvoted by the other four tasks.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .config import DecisionSettings
from .harness import HarnessDiff
from .metrics import Comparison
from .stats import resolvable_effect

Verdict = Literal["POSITIVE", "NEGATIVE", "INCONCLUSIVE", "NOT_COMPARABLE"]


class Reason(BaseModel):
    """One gate's contribution to the verdict. Every verdict is fully explained by these."""

    gate: str
    outcome: str
    detail: str
    severity: str = "info"


class Decision(BaseModel):
    verdict: Verdict
    headline: str
    reasons: list[Reason] = Field(default_factory=list)
    manual_review_required: bool = False
    manual_review_reasons: list[str] = Field(default_factory=list)
    primary_direction: str = "flat"
    guardrail_breaches: list[str] = Field(default_factory=list)
    thresholds: dict = Field(default_factory=dict)
    resolvable_effect: float | None = None
    underpowered: bool = False


def _gate_comparability(diff: HarnessDiff, comparison: Comparison) -> list[Reason]:
    """Gate 0 — is this a controlled comparison at all?

    If the model changed at the same time as the harness, any difference is unattributable.
    The tool refuses to answer rather than crediting a model upgrade to a new AGENTS.md.
    """
    reasons: list[Reason] = []
    if diff.model_changed:
        change = next(c for c in diff.config if c.key == "model")
        reasons.append(Reason(
            gate="comparability", outcome="blocked", severity="critical",
            detail=(f"the model changed between arms ({change.before} -> {change.after}). "
                    f"Any difference confounds the harness with the model; re-run with the "
                    f"model held constant, or compare each harness under both models."),
        ))
    if diff.agent_changed:
        reasons.append(Reason(
            gate="comparability", outcome="blocked", severity="critical",
            detail="the agent adapter changed between arms; the comparison is not controlled",
        ))
    if diff.identical:
        reasons.append(Reason(
            gate="comparability", outcome="warning", severity="warning",
            detail="the two harnesses are byte-identical; any measured difference is noise",
        ))
    if comparison.n_tasks == 0:
        reasons.append(Reason(
            gate="comparability", outcome="blocked", severity="critical",
            detail="no task ran under both harnesses",
        ))
    return reasons


def decide(
    comparison: Comparison, diff: HarnessDiff, settings: DecisionSettings | None = None
) -> Decision:
    """Apply the gates in order and produce a verdict that is entirely explained by them."""
    settings = settings or DecisionSettings()
    reasons: list[Reason] = []

    # ---- Gate 0: comparability -------------------------------------------------
    blocking = _gate_comparability(diff, comparison)
    reasons.extend(blocking)
    if any(r.outcome == "blocked" for r in blocking):
        return Decision(
            verdict="NOT_COMPARABLE",
            headline="The two arms differ in more than the harness, so no verdict is possible.",
            reasons=reasons, thresholds=settings.model_dump(),
        )

    # ---- Gate 1: blocking regressions ------------------------------------------
    critical = [f for f in comparison.regressions if f.severity == "critical"]
    for finding in critical:
        reasons.append(Reason(
            gate="blocker", outcome="failed", severity="critical",
            detail=f"critical regression — {finding.message}",
        ))
    if not critical:
        reasons.append(Reason(
            gate="blocker", outcome="passed",
            detail="no task went from passing under baseline to failing under candidate",
        ))

    # ---- Gate 2: integrity -----------------------------------------------------
    review = [f.message for f in comparison.manual_review]
    if review:
        reasons.append(Reason(
            gate="integrity", outcome="flagged", severity="warning",
            detail=f"{len(review)} finding(s) need a human before this verdict is acted on",
        ))

    # ---- Gate 3: the primary metric --------------------------------------------
    primary = comparison.deltas.get("correctness")
    direction = "flat"
    if primary is None or primary.absolute is None:
        reasons.append(Reason(gate="primary", outcome="unmeasured",
                              detail="functional correctness could not be measured"))
    else:
        low, high = primary.ci_low, primary.ci_high
        pp = primary.absolute * 100
        span = (
            f"95% CI {low * 100:+.1f} to {high * 100:+.1f}pp"
            if low is not None and high is not None else "no interval"
        )
        if low is not None and low > settings.min_effect:
            direction = "improved"
            reasons.append(Reason(
                gate="primary", outcome="improved",
                detail=(f"functional correctness improved by {pp:+.1f} percentage points "
                        f"({span}), clearing the "
                        f"{settings.min_effect * 100:.0f}pp materiality threshold"),
            ))
        elif high is not None and high < -settings.min_effect:
            direction = "regressed"
            reasons.append(Reason(
                gate="primary", outcome="regressed", severity="critical",
                detail=f"functional correctness fell by {pp:+.1f} percentage points ({span})",
            ))
        else:
            reasons.append(Reason(
                gate="primary", outcome="flat",
                detail=(f"functional correctness moved {pp:+.1f} percentage points but the "
                        f"interval does not clear the {settings.min_effect * 100:.0f}pp "
                        f"threshold ({span}) - this is consistent with no real change"),
            ))

    # ---- Gate 4: guardrails ----------------------------------------------------
    breaches: list[str] = []

    def guard(key: str, tolerance: float, *, label: str, lower_is_better: bool) -> None:
        delta = comparison.deltas.get(key)
        if delta is None or delta.relative is None:
            return
        breached = delta.relative > tolerance if lower_is_better else delta.relative < -tolerance
        if breached:
            breaches.append(
                f"{label} moved {delta.relative*100:+.1f}%, beyond the "
                f"{tolerance*100:.0f}% tolerance"
            )

    guard("cost", settings.cost_tolerance, label="cost per task", lower_is_better=True)
    guard("duration", settings.duration_tolerance, label="wall-clock per task",
          lower_is_better=True)
    guard("quality", settings.quality_tolerance, label="objective quality", lower_is_better=False)
    guard("reliability", settings.reliability_tolerance, label="reliability",
          lower_is_better=False)

    judge_delta = comparison.deltas.get("judge")
    if judge_delta is not None and judge_delta.absolute is not None:
        if judge_delta.absolute < -settings.judge_tolerance:
            breaches.append(
                f"judged requirement adherence fell {judge_delta.absolute:+.2f} points "
                f"(subjective signal — confirm before acting)"
            )

    for breach in breaches:
        reasons.append(Reason(gate="guardrail", outcome="breached", severity="warning",
                              detail=breach))
    if not breaches:
        reasons.append(Reason(gate="guardrail", outcome="passed",
                              detail="cost, duration, objective quality and reliability all "
                                     "stayed within tolerance"))

    # ---- Combine ---------------------------------------------------------------
    cost = comparison.deltas.get("cost")
    cost_improved = (
        cost is not None and cost.relative is not None
        and cost.relative < -settings.cost_tolerance
    )

    if critical or direction == "regressed":
        verdict: Verdict = "NEGATIVE"
        headline = ("The candidate harness made things worse. Do not roll it out."
                    if direction == "regressed" else
                    "The candidate harness broke a task the baseline handled. Do not roll it out.")
    elif direction == "improved" and not breaches:
        verdict, headline = "POSITIVE", (
            "The candidate harness improved functional correctness materially, with no "
            "regression and no guardrail breach."
        )
    elif direction == "improved" and breaches:
        verdict, headline = "INCONCLUSIVE", (
            "The candidate harness improved correctness but breached a guardrail. Whether "
            "that trade is worth making is a judgement this tool will not make for you."
        )
    elif direction == "flat" and cost_improved and not breaches:
        verdict, headline = "POSITIVE", (
            "Correctness is unchanged but the candidate harness costs materially less, with "
            "no guardrail breach — an efficiency gain at equal quality."
        )
    elif breaches:
        # Harm is demonstrated (a guardrail moved, measurably); benefit is not. That
        # asymmetry is the whole justification for calling this NEGATIVE rather than
        # INCONCLUSIVE - but the headline must not let a reader conclude that correctness
        # got worse, because the point estimate here may well be positive.
        point = ""
        if primary is not None and primary.absolute is not None:
            point = (
                f" Correctness moved {primary.absolute * 100:+.1f}pp, but the interval "
                f"contains zero, so that movement is not evidence of anything."
            )
        verdict, headline = "NEGATIVE", (
            "The candidate harness costs measurably more without a demonstrated benefit: "
            + "; ".join(breaches) + "." + point
        )
    else:
        verdict, headline = "INCONCLUSIVE", (
            "This evaluation did not find a difference large enough to act on. That is not "
            "evidence the change is worthless — it is evidence this experiment was too small "
            "to see it."
        )

    detectable = resolvable_effect(comparison.n_tasks, comparison.n_reps)
    underpowered = bool(
        detectable is None
        or (primary is not None and primary.absolute is not None
            and abs(primary.absolute) < detectable and verdict == "INCONCLUSIVE")
    )

    return Decision(
        verdict=verdict, headline=headline, reasons=reasons,
        manual_review_required=bool(review), manual_review_reasons=review,
        primary_direction=direction, guardrail_breaches=breaches,
        thresholds=settings.model_dump(), resolvable_effect=detectable,
        underpowered=underpowered,
    )

"""Metrics aggregation, regression detection, and the decision gates."""

from agent_eval.config import DecisionSettings
from agent_eval.decide import decide
from agent_eval.harness import ConfigChange, HarnessDiff, HarnessFileChange
from agent_eval.metrics import compare, summarise_arm, task_arm_metrics
from agent_eval.models import CheckOutcome, JudgeSummary, RunRecord, TamperInfo
from agent_eval.runners.base import AgentUsage


def run(task, arm, rep, *, passed=True, quality=True, duration=100.0, cost=0.1,
        judge=None, tamper=False, source="actual"):
    checks = [
        CheckOutcome(id="tests", type="pytest", dimension="correctness", passed=passed,
                     exit_code=0 if passed else 1, duration_s=1.0, command=["pytest"]),
        CheckOutcome(id="lint", type="command", dimension="quality", passed=quality,
                     exit_code=0 if quality else 1, duration_s=1.0, command=["ruff"]),
    ]
    return RunRecord(
        run_id=f"{task}-{arm}-{rep}", evaluation_id="e", task_id=task, arm=arm, rep=rep,
        order_index=rep, harness_id="h" * 64, harness_short_id=f"hns_{arm[:4]}",
        duration_s=duration, checks=checks,
        usage=AgentUsage(source=source, cost_usd=cost, input_tokens=100, output_tokens=10),
        tamper=TamperInfo(detected=tamper, paths=["tests/test_x.py"] if tamper else []),
        judge=JudgeSummary(score=judge) if judge is not None else None,
    )


def arm(name, spec, **kwargs):
    """spec: {task_id: [pass_bool, ...]} -> an ArmSummary."""
    metrics = []
    for task, outcomes in spec.items():
        records = [
            run(task, name, i + 1, passed=p, **kwargs) for i, p in enumerate(outcomes)
        ]
        metrics.append(task_arm_metrics(task, name, records))
    return summarise_arm(name, metrics, f"hns_{name[:4]}")


DIFF = HarnessDiff(
    baseline_id="hns_a", candidate_id="hns_b",
    files=[HarnessFileChange(path="AGENTS.md", change="modified")], config=[],
)


# --- aggregation ------------------------------------------------------------

def test_a_run_with_no_correctness_checks_does_not_count_as_a_pass():
    """`all()` over an empty list is True; that must not become a silent success."""
    record = RunRecord(run_id="r", evaluation_id="e", task_id="t", arm="baseline", rep=1,
                       order_index=0, harness_id="h", harness_short_id="hns_x")
    assert record.correctness_passed is False


def test_pass_rate_and_consistency():
    metrics = task_arm_metrics("t1", "baseline",
                               [run("t1", "baseline", i, passed=p)
                                for i, p in enumerate([True, True, False])])
    assert metrics.passes == 2
    assert round(metrics.pass_rate, 3) == 0.667
    assert round(metrics.consistency, 3) == 0.667
    assert metrics.flaky is True
    assert metrics.modal_pass is True


def test_a_task_that_works_half_the_time_is_not_a_pass():
    metrics = task_arm_metrics("t1", "baseline",
                               [run("t1", "baseline", i, passed=p)
                                for i, p in enumerate([True, False])])
    assert metrics.pass_rate == 0.5
    assert metrics.modal_pass is False


def test_consistent_failure_is_still_consistent():
    metrics = task_arm_metrics("t1", "baseline",
                               [run("t1", "baseline", i, passed=False) for i in range(3)])
    assert metrics.consistency == 1.0 and metrics.flaky is False


def test_median_cost_ignores_an_outlier_run():
    records = [run("t1", "baseline", i, cost=c) for i, c in enumerate([0.1, 0.1, 9.9])]
    assert task_arm_metrics("t1", "baseline", records).median_cost_usd == 0.1


def test_effective_sample_size_is_tasks_not_runs():
    """Five tasks x three reps is n=5, not n=15. The whole statistical argument."""
    summary = arm("baseline", {f"t{i}": [True, True, False] for i in range(5)})
    assert summary.n_tasks == 5
    assert summary.pooled_runs == 15
    comparison = compare(summary, summary)
    assert comparison.n_tasks == 5 and comparison.n_reps == 3


# --- regressions ------------------------------------------------------------

def test_a_critical_regression_is_detected():
    base = arm("baseline", {"t1": [True, True, True], "t2": [True, True, True]})
    cand = arm("candidate", {"t1": [True, True, True], "t2": [False, False, False]})
    comparison = compare(base, cand)
    assert [f.task_id for f in comparison.regressions] == ["t2"]
    assert comparison.regressions[0].severity == "critical"


def test_an_improvement_is_detected():
    base = arm("baseline", {"t1": [False, False, False]})
    cand = arm("candidate", {"t1": [True, True, True]})
    assert [f.task_id for f in compare(base, cand).improvements] == ["t1"]


def test_newly_flaky_is_flagged_as_a_reliability_regression():
    base = arm("baseline", {"t1": [True, True, True]})
    cand = arm("candidate", {"t1": [True, True, False]})
    comparison = compare(base, cand)
    kinds = {f.kind for f in comparison.regressions}
    assert "reliability" in kinds
    assert "became-flaky" in comparison.tasks[0].flags


def test_tamper_reaches_the_manual_review_queue():
    base = arm("baseline", {"t1": [True]})
    cand = arm("candidate", {"t1": [True]}, tamper=True)
    findings = compare(base, cand).manual_review
    assert any(f.kind == "tamper" and "t1" in f.message for f in findings)


def test_passing_tests_with_a_low_judge_score_is_flagged():
    """The most valuable cell in the tool: measured and judged signals disagree."""
    base = arm("baseline", {"t1": [True]}, judge=4.0)
    cand = arm("candidate", {"t1": [True]}, judge=2.5)
    findings = compare(base, cand, judge_enabled=True).manual_review
    assert any(f.kind == "disagreement" and "passes the tests" in f.message for f in findings)


def test_failing_tests_with_a_high_judge_score_points_at_the_benchmark():
    base = arm("baseline", {"t1": [False]}, judge=4.5)
    cand = arm("candidate", {"t1": [False]}, judge=4.5)
    findings = compare(base, cand, judge_enabled=True).manual_review
    assert any("suspect the benchmark" in f.message for f in findings)


# --- decision gates ---------------------------------------------------------

def test_a_model_change_refuses_a_verdict():
    """Gate 0. Attributing a model upgrade to a new AGENTS.md is the failure this
    whole tool exists to prevent."""
    diff = HarnessDiff(baseline_id="a", candidate_id="b", files=[], config=[
        ConfigChange(key="model", before="claude-sonnet-4-5", after="claude-opus-4-1")
    ])
    base = arm("baseline", {"t1": [False]})
    cand = arm("candidate", {"t1": [True]})
    decision = decide(compare(base, cand), diff)
    assert decision.verdict == "NOT_COMPARABLE"
    assert any("model changed" in r.detail for r in decision.reasons)


def test_a_critical_regression_vetoes_an_average_improvement():
    """Four tasks improve, one breaks. A weighted score would call this positive."""
    base = arm("baseline", {f"t{i}": [False, False, False] for i in range(4)}
               | {"t9": [True, True, True]})
    cand = arm("candidate", {f"t{i}": [True, True, True] for i in range(4)}
               | {"t9": [False, False, False]})
    decision = decide(compare(base, cand), DIFF)
    assert decision.verdict == "NEGATIVE"
    assert any(r.gate == "blocker" and r.outcome == "failed" for r in decision.reasons)


def test_a_clear_improvement_is_positive():
    base = arm("baseline", {f"t{i}": [False, False, False] for i in range(5)})
    cand = arm("candidate", {f"t{i}": [True, True, True] for i in range(5)})
    decision = decide(compare(base, cand), DIFF)
    assert decision.verdict == "POSITIVE"
    assert decision.primary_direction == "improved"


def test_a_small_improvement_is_inconclusive_not_positive():
    """One task out of five moving by one repetition is noise, and the tool says so."""
    base = arm("baseline", {f"t{i}": [True, True, True] for i in range(4)}
               | {"t9": [False, False, False]})
    cand = arm("candidate", {f"t{i}": [True, True, True] for i in range(4)}
               | {"t9": [True, False, False]})
    decision = decide(compare(base, cand), DIFF)
    assert decision.verdict == "INCONCLUSIVE"
    assert decision.underpowered


def test_identical_harnesses_are_flagged_even_when_results_differ():
    diff = HarnessDiff(baseline_id="a", candidate_id="a", files=[], config=[])
    base = arm("baseline", {"t1": [True]})
    cand = arm("candidate", {"t1": [True]})
    decision = decide(compare(base, cand), diff)
    assert any("byte-identical" in r.detail for r in decision.reasons)


def test_cost_saved_at_equal_correctness_is_positive():
    base = arm("baseline", {f"t{i}": [True, True, True] for i in range(5)}, cost=1.0)
    cand = arm("candidate", {f"t{i}": [True, True, True] for i in range(5)}, cost=0.5)
    decision = decide(compare(base, cand), DIFF)
    assert decision.verdict == "POSITIVE"
    assert "efficiency" in decision.headline


def test_cost_blown_without_benefit_is_negative_but_says_so_precisely():
    base = arm("baseline", {f"t{i}": [True, True, True] for i in range(5)}, cost=0.5)
    cand = arm("candidate", {f"t{i}": [True, True, True] for i in range(5)}, cost=1.0)
    decision = decide(compare(base, cand), DIFF)
    assert decision.verdict == "NEGATIVE"
    assert "cost per task" in decision.headline
    # It must not let a reader conclude correctness fell when it did not.
    assert "not evidence of anything" in decision.headline


def test_thresholds_are_configurable_and_recorded():
    base = arm("baseline", {f"t{i}": [True, True, True] for i in range(5)}, cost=1.0)
    cand = arm("candidate", {f"t{i}": [True, True, True] for i in range(5)}, cost=1.1)
    lenient = decide(compare(base, cand), DIFF, DecisionSettings(cost_tolerance=0.50))
    strict = decide(compare(base, cand), DIFF, DecisionSettings(cost_tolerance=0.01))
    assert lenient.verdict == "INCONCLUSIVE" and not lenient.guardrail_breaches
    assert strict.verdict == "NEGATIVE" and strict.guardrail_breaches
    assert strict.thresholds["cost_tolerance"] == 0.01


def test_every_verdict_is_fully_explained_by_its_reasons():
    base = arm("baseline", {f"t{i}": [True, False, True] for i in range(5)})
    cand = arm("candidate", {f"t{i}": [True, True, True] for i in range(5)})
    decision = decide(compare(base, cand), DIFF)
    gates = {r.gate for r in decision.reasons}
    assert {"blocker", "primary", "guardrail"} <= gates


# --- runs that never executed -----------------------------------------------

def invalid_run(task, arm, rep):
    record = run(task, arm, rep, passed=False)
    record.invalid = True
    record.invalid_reason = "agent reported an error: OAuth session expired"
    return record


def test_an_invalid_run_is_excluded_not_counted_as_a_failure():
    """Counting a run the agent never attempted as a failure turns an expired credential
    into evidence about harness quality."""
    records = [run("t1", "baseline", 1), run("t1", "baseline", 2), invalid_run("t1", "baseline", 3)]
    metrics = task_arm_metrics("t1", "baseline", records)
    assert metrics.reps == 2, "the invalid run must leave the denominator"
    assert metrics.pass_rate == 1.0
    assert metrics.invalid_runs == 1


def test_a_task_where_nothing_executed_reports_no_pass_rate():
    metrics = task_arm_metrics("t1", "baseline", [invalid_run("t1", "baseline", i) for i in (1, 2)])
    assert metrics.reps == 0 and metrics.invalid_runs == 2
    assert metrics.pass_rate == 0.0


def test_invalid_runs_block_the_verdict():
    """The sample must never shrink silently: if runs did not execute, the tool refuses
    to answer rather than reporting a result from whatever survived."""
    base = arm("baseline", {"t1": [True, True, True]})
    cand = arm("candidate", {"t1": [True, True, True]})
    cand.invalid_runs = 2
    decision = decide(compare(base, cand), DIFF)
    assert decision.verdict == "NOT_COMPARABLE"
    assert any("never executed" in r.detail for r in decision.reasons)


def test_an_invalid_run_cannot_pass_its_checks():
    record = invalid_run("t1", "baseline", 1)
    record.checks[0].passed = True
    assert record.correctness_passed is False


# --- a design too small to resolve anything must not produce a verdict ------

def test_a_single_task_cannot_produce_a_verdict():
    """Regression test, from a real smoke run. One task, one repetition: the primary gate
    correctly refused to conclude ("interval -100 to +100pp"), and then the guardrail gate
    concluded anyway from a bare point estimate and returned NEGATIVE. Rigorous about
    correctness and cavalier about cost, in the same verdict."""
    base = arm("baseline", {"t1": [True]}, cost=0.16, duration=42.0)
    cand = arm("candidate", {"t1": [True]}, cost=0.29, duration=78.0)
    decision = decide(compare(base, cand), DIFF)
    assert decision.verdict == "INCONCLUSIVE"
    assert decision.underpowered
    assert "Too few tasks" in decision.headline
    assert any(r.gate == "power" for r in decision.reasons)


def test_a_guardrail_breach_needs_the_interval_to_exclude_zero():
    """The point estimate exceeding tolerance is necessary, not sufficient."""
    base = arm("baseline", {f"t{i}": [True] for i in range(5)}, cost=0.10)
    cand = arm("candidate", {f"t{i}": [True] for i in range(5)}, cost=0.30)
    comparison = compare(base, cand)
    # Force the interval to straddle zero while leaving the point estimate large.
    comparison.deltas["cost"].ci_low, comparison.deltas["cost"].ci_high = -0.5, 0.5
    decision = decide(comparison, DIFF)
    assert not decision.guardrail_breaches
    assert any(r.outcome == "not-evidenced" for r in decision.reasons)


def test_an_evidenced_guardrail_breach_still_fires():
    """The fix must not gut the guardrail: a consistent cost increase across tasks
    produces an interval that excludes zero, and still breaches."""
    base = arm("baseline", {f"t{i}": [True, True, True] for i in range(5)}, cost=0.10)
    cand = arm("candidate", {f"t{i}": [True, True, True] for i in range(5)}, cost=0.30)
    decision = decide(compare(base, cand), DIFF)
    assert decision.guardrail_breaches
    assert decision.verdict == "NEGATIVE"


def test_an_observed_regression_still_counts_at_low_n():
    """Watching a task break is concrete evidence, and the conservative bias is right:
    a blocker keeps its NEGATIVE even when the design is too small for a verdict."""
    base = arm("baseline", {"t1": [True, True, True]})
    cand = arm("candidate", {"t1": [False, False, False]})
    decision = decide(compare(base, cand), DIFF)
    assert decision.verdict == "NEGATIVE"
    assert any(r.gate == "blocker" and r.outcome == "failed" for r in decision.reasons)


def test_the_minimum_task_count_is_configurable():
    base = arm("baseline", {"t1": [True], "t2": [True]})
    cand = arm("candidate", {"t1": [True], "t2": [True]})
    comparison = compare(base, cand)

    strict = decide(comparison, DIFF, DecisionSettings(min_tasks_for_verdict=5))
    assert strict.underpowered and "Too few tasks" in strict.headline

    lenient = decide(comparison, DIFF, DecisionSettings(min_tasks_for_verdict=2))
    assert "Too few tasks" not in lenient.headline
    assert not any(r.gate == "power" for r in lenient.reasons)

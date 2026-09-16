from agent_eval.stats import (
    median,
    paired_bootstrap_ci,
    percentile,
    relative_change,
    resolvable_effect,
    stdev,
    wilson_interval,
)


def test_wilson_never_claims_certainty_from_a_small_sample():
    """The normal approximation returns [1.0, 1.0] for 15/15, claiming certainty from
    fifteen observations. That is the reason this tool uses Wilson."""
    interval = wilson_interval(15, 15)
    assert interval.low < 1.0
    assert interval.high == 1.0
    assert interval.low > 0.75


def test_wilson_stays_inside_zero_and_one():
    for successes, total in [(0, 5), (5, 5), (1, 3), (0, 1)]:
        interval = wilson_interval(successes, total)
        assert 0.0 <= interval.low <= interval.high <= 1.0


def test_wilson_narrows_as_the_sample_grows():
    small = wilson_interval(8, 10)
    large = wilson_interval(80, 100)
    assert (large.high - large.low) < (small.high - small.low)


def test_wilson_with_no_data_is_maximally_uncertain():
    assert wilson_interval(0, 0).as_tuple() == (0.0, 1.0)


def test_bootstrap_interval_contains_the_point_estimate():
    deltas = [0.0, 0.33, 0.33, 0.67, 0.0]
    interval = paired_bootstrap_ci(deltas, samples=4000, seed=3)
    assert interval.low <= sum(deltas) / len(deltas) <= interval.high


def test_bootstrap_on_a_weak_effect_does_not_exclude_zero():
    """One task moving out of five is not evidence."""
    assert not paired_bootstrap_ci([0.33, 0, 0, 0, 0], samples=4000, seed=3).excludes_zero()


def test_bootstrap_on_a_consistent_effect_excludes_zero():
    assert paired_bootstrap_ci([0.33, 0.33, 0.67, 0.33, 0.67], samples=4000, seed=3).excludes_zero()


def test_bootstrap_with_one_task_refuses_to_bound_anything():
    """A single cluster carries no information about between-task variation."""
    assert paired_bootstrap_ci([0.5]).as_tuple() == (-1.0, 1.0)


def test_bootstrap_is_deterministic_under_a_seed():
    a = paired_bootstrap_ci([0.1, 0.2, 0.3], samples=2000, seed=11)
    b = paired_bootstrap_ci([0.1, 0.2, 0.3], samples=2000, seed=11)
    assert a.as_tuple() == b.as_tuple()


def test_median_ignores_a_single_outlier():
    """Durations are long-tailed; one retry storm must not move the typical run."""
    assert median([60, 62, 64, 600]) == 63


def test_percentile_and_stdev():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9) == 9
    assert percentile([], 0.9) == 0.0
    assert stdev([2, 2, 2]) == 0.0
    assert stdev([1]) == 0.0


def test_relative_change_is_none_rather_than_zero_when_undefined():
    assert relative_change(0, 5) is None
    assert relative_change(None, 5) is None
    assert relative_change(10, 12) == 0.2


def test_resolvable_effect_matches_the_default_materiality_threshold():
    """The tool ships min_effect=0.20 for a 5x3 design; that number is not a guess."""
    assert resolvable_effect(5, 3) == 0.2


def test_more_repetitions_resolve_smaller_effects():
    """The whole argument for repeating runs, in one assertion."""
    one_rep = resolvable_effect(5, 1)
    three_reps = resolvable_effect(5, 3)
    assert one_rep is not None and three_reps is not None
    assert three_reps < one_rep


def test_more_tasks_resolve_smaller_effects():
    assert resolvable_effect(10, 3) < resolvable_effect(5, 3)


def test_degenerate_designs_return_none():
    assert resolvable_effect(0, 3) is None

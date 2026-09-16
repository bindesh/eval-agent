"""The small amount of statistics this tool needs, and no more.

Two principles decide everything here:

1. **The unit of analysis is the task, not the run.** Three repetitions of one task share
   a fixture, a prompt and a difficulty; they are not three independent samples. Pooling
   fifteen runs as fifteen samples overstates the sample size roughly threefold, narrows
   every interval accordingly, and turns noise into confident conclusions.
2. **Pair, because the tasks are identical across arms.** A paired comparison removes
   between-task difficulty variance exactly, which is the dominant source of noise when
   one task is trivial and another is hard.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Interval:
    """A confidence interval. ``low is None`` means it could not be computed."""

    low: float
    high: float
    confidence: float = 0.95

    def excludes_zero(self) -> bool:
        return self.low > 0 or self.high < 0

    def as_tuple(self) -> tuple[float, float]:
        return self.low, self.high


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median(values: list[float]) -> float:
    """Preferred over the mean for durations and costs, which are long-tailed: one retry
    storm should not move the number that represents a typical run."""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile. Used for p90 duration as a tail guardrail."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = mean(values)
    return math.sqrt(sum((v - average) ** 2 for v in values) / (len(values) - 1))


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> Interval:
    """Wilson score interval for a proportion.

    Chosen over the textbook normal approximation because that approximation is simply
    wrong at the sample sizes and the extremes this tool lives at: for 15/15 successes it
    produces the interval [1.0, 1.0], claiming certainty from fifteen observations.
    Wilson stays inside [0, 1] and keeps sensible width near the boundaries.
    """
    if total <= 0:
        return Interval(0.0, 1.0, confidence)
    z = 1.959963985 if confidence >= 0.95 else 1.644853627
    p = successes / total
    denominator = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    spread = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denominator
    return Interval(max(0.0, centre - spread), min(1.0, centre + spread), confidence)


def paired_bootstrap_ci(
    deltas: list[float], *, samples: int = 10_000, confidence: float = 0.95, seed: int = 0
) -> Interval:
    """Percentile bootstrap for the mean paired difference, resampling **tasks**.

    Why a bootstrap and not a t-test: with five tasks and a bounded, discrete outcome the
    normality assumption behind a t-test is badly violated, and the resulting interval is
    a fiction with decimal places. Resampling whole tasks also propagates the clustering —
    each draw takes a task with all of its repetitions — which is the thing that actually
    determines how much evidence we have.

    With n=5 the interval is wide. That is the correct answer, not a defect.
    """
    if not deltas:
        return Interval(0.0, 0.0, confidence)
    if len(deltas) == 1:
        # One task cannot bound anything. Return the widest honest interval.
        return Interval(-1.0, 1.0, confidence)

    rng = random.Random(seed)
    n = len(deltas)
    means = []
    for _ in range(samples):
        draw = [deltas[rng.randrange(n)] for _ in range(n)]
        means.append(sum(draw) / n)
    means.sort()
    alpha = (1 - confidence) / 2
    low = means[max(0, int(alpha * samples) - 1)]
    high = means[min(samples - 1, int((1 - alpha) * samples))]
    return Interval(low, high, confidence)


def relative_change(baseline: float | None, candidate: float | None) -> float | None:
    """Relative difference, or None when it is undefined rather than zero."""
    if baseline is None or candidate is None or baseline == 0:
        return None
    return (candidate - baseline) / abs(baseline)


def resolvable_effect(
    n_tasks: int, n_reps: int, *, samples: int = 2_000, confidence: float = 0.95, seed: int = 7
) -> float | None:
    """The smallest paired difference this design can actually resolve.

    Computed with the *same estimator the report uses*, not with a textbook formula: we
    ask how many tasks would have to improve by one repetition each before the bootstrap
    interval for the mean paired difference clears zero, and convert that back into a
    mean difference.

    An earlier version of this function used a closed-form approximation and returned 0.60
    for a design whose bootstrap comfortably resolved 0.27 — a number that contradicted
    the tool's own analysis. Deriving it from the estimator removes that class of error.

    Returns ``None`` when no effect short of every task flipping is resolvable.
    """
    if n_tasks <= 0 or n_reps <= 0:
        return None
    step = 1.0 / n_reps
    for improved in range(1, n_tasks + 1):
        deltas = [step] * improved + [0.0] * (n_tasks - improved)
        interval = paired_bootstrap_ci(
            deltas, samples=samples, confidence=confidence, seed=seed
        )
        if interval.low > 0:
            return sum(deltas) / n_tasks
    return None

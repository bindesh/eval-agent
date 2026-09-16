"""The golden check is the tool's own integrity test, so it gets tested hardest."""

import pytest

from agent_eval.benchmark import load_benchmark
from agent_eval.doctor import diagnose
from agent_eval.workspace import WorkspaceError


def _one(root):
    return diagnose(load_benchmark(root))[0]


def test_healthy_task_is_ok(mini_benchmark):
    result = _one(mini_benchmark())
    assert result.ok
    assert result.correctness_fails_on_pristine
    assert result.reference_passes
    assert result.problems() == []


def test_vacuous_task_is_detected(mini_benchmark):
    """Verification already passes on the untouched fixture: the task cannot
    distinguish two harnesses, and would silently dilute every measured delta."""
    result = _one(mini_benchmark(vacuous=True))
    assert not result.ok
    assert not result.correctness_fails_on_pristine
    assert any("VACUOUS" in p for p in result.problems())


def test_unsolvable_task_is_detected(mini_benchmark):
    """The reference solution cannot satisfy the checks, so neither can any agent."""
    result = _one(mini_benchmark(solvable=False))
    assert not result.ok
    assert not result.reference_passes
    assert any("UNSOLVABLE" in p for p in result.problems())


def test_task_without_a_reference_cannot_be_certified(mini_benchmark):
    result = _one(mini_benchmark(with_reference=False))
    assert not result.ok
    assert any("NO REFERENCE" in p for p in result.problems())


def test_diagnose_can_be_limited_to_named_tasks(mini_benchmark):
    spec = load_benchmark(mini_benchmark())
    assert [r.task_id for r in diagnose(spec, task_ids=["t1"])] == ["t1"]


def test_doctor_applies_benchmark_workspace_excludes(mini_benchmark):
    """Found in review: removing the kwarg at the doctor call sites broke no test. A pattern
    that hides fixture source must surface in doctor, which is where it would be caught."""
    benchmark = load_benchmark(mini_benchmark())
    benchmark = benchmark.model_copy(update={"workspace_excludes": ["src/"]})
    with pytest.raises(WorkspaceError, match="src/thing.py"):
        diagnose(benchmark)

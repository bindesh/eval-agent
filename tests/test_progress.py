"""The live status line. Display only, but a wrong ETA or a stale line misleads just the same."""

from agent_eval.progress import StageProgress, estimate_remaining, format_duration, format_eta
from agent_eval.runners import AgentActivity


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_no_estimate_is_invented_before_anything_has_finished():
    assert estimate_remaining([], total=30, current_elapsed=12.0) is None


def test_the_estimate_counts_time_already_spent_on_the_current_run():
    # two finished at 60s each, 28 left including the current one, which is 20s in
    assert estimate_remaining([60.0, 60.0], total=30, current_elapsed=20.0) == 60.0 * 28 - 20


def test_the_estimate_never_goes_negative_on_a_slow_last_run():
    assert estimate_remaining([10.0], total=2, current_elapsed=500.0) == 0.0


def test_durations_and_etas_are_formatted_for_a_human():
    assert format_duration(41) == "0:41"
    assert format_duration(3725) == "1h02m"
    assert format_eta(25) == "<1m left"
    assert format_eta(28 * 60 + 10) == "~28m left"


def test_the_status_line_shows_position_clock_turn_activity_and_eta():
    clock = FakeClock()
    progress = StageProgress(total=30, clock=clock)
    progress.start(1, "t01-export-csv baseline rep1")
    clock.now = 70.0
    assert progress.finish() == 70.0

    progress.start(2, "t01-export-csv candidate rep1")
    clock.now = 111.0
    progress.activity(AgentActivity(turn=7, summary="Edit src/customers/service.py"))
    assert progress.status_text() == (
        "[2/30] t01-export-csv candidate rep1 · 0:41 · turn 7 · "
        "Edit src/customers/service.py · ~33m left"
    )


def test_a_phase_replaces_the_last_agent_activity():
    clock = FakeClock()
    progress = StageProgress(total=2, clock=clock)
    progress.start(1, "t1 baseline rep1")
    progress.activity(AgentActivity(turn=3, summary="Bash $ pytest"))
    progress.phase("running checks")
    assert "running checks" in progress.status_text()
    assert "ETA after the first run" in progress.status_text()

"""Live progress for the long stages: agent runs and judging.

A real evaluation is ~30 agent runs of 60-90 s each. Printing only when a run finishes
left the terminal silent for minutes, and a silent terminal is indistinguishable from a
hung one. These renderables are redrawn by `rich.live.Live` a few times a second, so the
elapsed clock keeps moving even while the agent is thinking.

Display only: nothing here is written to disk or feeds a metric. The ETA in particular is
a naive mean of wall-clock time so far - good enough to decide whether to get coffee, and
deliberately not reported anywhere a reader might mistake it for a measurement.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from rich.console import Group, RenderableType
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .runners.base import AgentActivity


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}:{secs:02d}"


def format_eta(seconds: float) -> str:
    """Coarse on purpose: a naive mean does not deserve second-level precision."""
    minutes = int(round(seconds / 60))
    if minutes < 1:
        return "<1m left"
    hours, minutes = divmod(minutes, 60)
    return f"~{hours}h{minutes:02d}m left" if hours else f"~{minutes}m left"


def estimate_remaining(
    completed: list[float], total: int, current_elapsed: float | None
) -> float | None:
    """Seconds left for the whole plan, or None before any run has finished.

    The in-progress run counts as remaining minus the time it has already used, floored
    at zero, so the estimate does not jump when a slow run finally completes.
    """
    if not completed:
        return None
    mean = sum(completed) / len(completed)
    left = total - len(completed)
    estimate = mean * left - (current_elapsed or 0.0)
    return max(0.0, estimate)


@dataclass
class _Line:
    label: str
    started: float
    turn: int = 0
    activity: str = ""


@dataclass
class StageProgress:
    """A single live status line: `[4/30] label · 0:41 · turn 7 · Edit x.py · ~28m left`."""

    total: int
    noun: str = "run"
    clock: Callable[[], float] = time.monotonic
    completed: list[float] = field(default_factory=list)
    _current: _Line | None = None
    _index: int = 0
    _spinner: Spinner = field(default_factory=lambda: Spinner("dots", style="cyan"))

    def _now(self) -> float:
        return self.clock()

    def start(self, index: int, label: str, activity: str = "") -> None:
        self._index = index
        self._current = _Line(label=label, started=self._now(), activity=activity)

    def activity(self, activity: AgentActivity) -> None:
        if self._current is not None:
            self._current.turn = activity.turn
            self._current.activity = activity.summary

    def phase(self, text: str) -> None:
        if self._current is not None:
            self._current.activity = text

    def finish(self) -> float:
        """Record the finished item's wall-clock time and return it."""
        if self._current is None:
            return 0.0
        took = self._now() - self._current.started
        self.completed.append(took)
        self._current = None
        return took

    def status_text(self) -> str:
        if self._current is None:
            return ""
        elapsed = self._now() - self._current.started
        parts = [
            f"[{self._index}/{self.total}] {self._current.label}",
            format_duration(elapsed),
        ]
        if self._current.turn:
            parts.append(f"turn {self._current.turn}")
        if self._current.activity:
            parts.append(self._current.activity)
        remaining = estimate_remaining(self.completed, self.total, elapsed)
        parts.append(
            format_eta(remaining) if remaining is not None
            else f"ETA after the first {self.noun}"
        )
        return " · ".join(parts)

    def __rich__(self) -> RenderableType:
        if self._current is None:
            return Group()
        grid = Table.grid(padding=(0, 1))
        text = Text(self.status_text(), style="dim", overflow="ellipsis", no_wrap=True)
        grid.add_row(self._spinner, text)
        return grid

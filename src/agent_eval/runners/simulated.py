"""Simulated adapter — a deterministic stand-in agent for the offline demo.

The reviewer of this repository should be able to see a complete evaluation without an
API key and without spending money. The replay adapter covers that once real runs exist;
this adapter covers it before they do, and in CI.

What is simulated and what is not:

* **Simulated:** which attempt each run produces, and the duration/token/cost numbers.
  Those are declared in the benchmark, not measured. Every one of them is labelled
  ``simulated`` all the way through to the report.
* **Real:** the workspace, the patch, and every objective check. The attempts are real
  Python written to be plausibly right or plausibly wrong, and `pytest`, `ruff` and
  `mypy` genuinely run against them.

So the demo exercises the whole pipeline on real code, and lies about nothing.
"""

from __future__ import annotations

import hashlib
import random
import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .base import AgentRunner, AgentRunRequest, AgentRunResult, AgentUsage


class Attempt(BaseModel):
    """One canned outcome an agent might produce for a task."""

    id: str
    description: str = ""
    directory: Path
    use_reference: bool = False
    weights: dict[str, float] = Field(default_factory=dict)
    duration_s: dict[str, float] = Field(default_factory=dict)
    usage: dict[str, float] = Field(default_factory=dict)
    # Per-arm multiplier on the usage figures: a harness that tells the agent to read
    # more context and verify before finishing genuinely consumes more tokens.
    usage_scale: dict[str, float] = Field(default_factory=dict)

    def weight_for(self, arm: str) -> float:
        return float(self.weights.get(arm, 0.0))


def load_attempts(task_dir: Path) -> list[Attempt]:
    """Read ``simulations/*/attempt.yaml`` for one task."""
    root = Path(task_dir) / "simulations"
    if not root.is_dir():
        return []
    attempts: list[Attempt] = []
    for directory in sorted(d for d in root.iterdir() if (d / "attempt.yaml").exists()):
        data = yaml.safe_load((directory / "attempt.yaml").read_text()) or {}
        attempts.append(Attempt(directory=directory, **data))
    return attempts


class SimulatedRunner(AgentRunner):
    """Picks an attempt by seeded draw and copies it into the workspace."""

    name = "simulated"

    def __init__(self, benchmark_dir: Path, *, seed: int = 0) -> None:
        self.benchmark_dir = Path(benchmark_dir)
        self.seed = seed

    def _rng(self, request: AgentRunRequest) -> random.Random:
        """Seeded per (seed, task, arm, rep) so an evaluation is reproducible run to run,
        while different repetitions still differ — which is the point of repeating them."""
        key = f"{self.seed}|{request.task_id}|{request.arm}|{request.rep}"
        return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:16], 16))

    def _choose(self, attempts: list[Attempt], request: AgentRunRequest) -> Attempt | None:
        weighted = [(a, a.weight_for(request.arm)) for a in attempts]
        weighted = [(a, w) for a, w in weighted if w > 0]
        if not weighted:
            return None
        total = sum(w for _, w in weighted)
        draw = self._rng(request).random() * total
        upto = 0.0
        for attempt, weight in weighted:
            upto += weight
            if draw <= upto:
                return attempt
        return weighted[-1][0]  # pragma: no cover - float edge

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        task_dir = self.benchmark_dir / "tasks" / request.task_id
        attempts = load_attempts(task_dir)
        attempt = self._choose(attempts, request)

        if attempt is None:
            return AgentRunResult(
                adapter=self.name, exit_code=1, simulated=True,
                usage=AgentUsage(source="simulated"),
                note=f"no simulated attempt defined for arm {request.arm!r}",
            )

        source = task_dir / "reference" if attempt.use_reference else attempt.directory / "files"
        copied: list[str] = []
        if source.is_dir():
            for item in sorted(source.rglob("*")):
                if item.is_dir() or "__pycache__" in item.parts:
                    continue
                target = Path(request.workspace) / item.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                copied.append(str(item.relative_to(source)))

        rng = self._rng(request)
        base_duration = float(
            attempt.duration_s.get(request.arm, attempt.duration_s.get("default", 60.0))
        )
        duration = round(base_duration * rng.uniform(0.82, 1.24), 1)
        scale = rng.uniform(0.85, 1.2) * float(attempt.usage_scale.get(request.arm, 1.0))

        def scaled(key: str) -> int | None:
            value = attempt.usage.get(key)
            return int(value * scale) if value is not None else None

        cost = attempt.usage.get("cost_usd")
        usage = AgentUsage(
            source="simulated",
            input_tokens=scaled("input_tokens"),
            output_tokens=scaled("output_tokens"),
            cache_read_tokens=scaled("cache_read_tokens"),
            cost_usd=round(cost * scale, 4) if cost is not None else None,
            turns=int(attempt.usage["turns"]) if "turns" in attempt.usage else None,
        )

        return AgentRunResult(
            adapter=self.name, adapter_version="simulated-1", exit_code=0,
            duration_s=duration, simulated=True, usage=usage,
            model_reported=request.model,
            stdout=(
                f"[SIMULATED AGENT] attempt={attempt.id}\n"
                f"{attempt.description}\nwrote: {', '.join(copied) or '(nothing)'}\n"
            ),
            note=f"simulated attempt {attempt.id!r}",
            raw={"attempt": attempt.id, "files": copied},
        )

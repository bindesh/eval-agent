"""Evaluation configuration.

Every threshold the verdict depends on lives here and is written into the report, so a
reader can see the rules the conclusion was reached under rather than inferring them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Order = Literal["interleaved", "blocked", "random"]


class EvaluationSettings(BaseModel):
    name: str = "unnamed evaluation"
    benchmark: Path
    baseline: Path
    candidate: Path
    runs: int = 3
    order: Order = "interleaved"
    seed: int = 0
    output_dir: Path = Path("runs")
    tasks: list[str] = Field(default_factory=list)


class AgentSettings(BaseModel):
    adapter: Literal["claude-code", "replay", "simulated"] = "simulated"
    executable: str = "claude"
    model: str | None = None
    cassette_dir: Path | None = None
    record: bool = False
    timeout_seconds: int | None = None


class JudgeSettings(BaseModel):
    enabled: bool = False
    provider: Literal["anthropic", "heuristic", "mock"] = "anthropic"
    model: str = "claude-sonnet-4-5"
    prompt_version: str = "v1"
    self_consistency: int = 2
    max_patch_chars: int = 24_000
    temperature: float = 0.0


class DecisionSettings(BaseModel):
    """Thresholds for the gated decision rules.

    Defaults are deliberately conservative: with five tasks and three repetitions this
    tool can only resolve large effects, so `min_effect` is set where a difference is
    both statistically visible and worth acting on.
    """

    min_effect: float = 0.20
    cost_tolerance: float = 0.15
    duration_tolerance: float = 0.25
    reliability_tolerance: float = 0.15
    quality_tolerance: float = 0.15
    judge_tolerance: float = 0.50
    # A judge's absolute scale is not calibrated, and different judges sit at different
    # central tendencies. These two thresholds decide what counts as a high or a low
    # judged score, and they are used by BOTH the manual-review flags and the
    # measured-vs-judged matrix - an earlier version used different numbers in each
    # place, and produced a report that contradicted itself. Calibrate them against the
    # judge's observed score distribution before trusting either.
    judge_high_threshold: float = 4.0
    judge_low_threshold: float = 3.0
    bootstrap_samples: int = 10_000
    confidence: float = 0.95


class Config(BaseModel):
    evaluation: EvaluationSettings
    agent: AgentSettings = Field(default_factory=AgentSettings)
    judge: JudgeSettings = Field(default_factory=JudgeSettings)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)
    source_path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> Config:
        """Load a YAML config; relative paths resolve against the config file."""
        path = Path(path).resolve()
        data = yaml.safe_load(path.read_text()) or {}
        config = cls(**data, source_path=path)
        base = path.parent
        ev = config.evaluation
        for field in ("benchmark", "baseline", "candidate", "output_dir"):
            value = getattr(ev, field)
            if not value.is_absolute():
                setattr(ev, field, (base / value).resolve())
        if config.agent.cassette_dir and not config.agent.cassette_dir.is_absolute():
            config.agent.cassette_dir = (base / config.agent.cassette_dir).resolve()
        return config

"""The LLM judge.

Some things cannot be measured by running a program: whether a patch satisfies the
requirement that was actually asked for, and whether it fits the codebase it lands in.
A judge gives us a signal there - but a judge is a model, with a model's failure modes,
so the design below is mostly about containing them.

Three biases, each designed out structurally rather than prompted away:

* **Positional bias** - a model asked "is A or B better?" favours one position. Avoided by
  scoring each patch on its own against an absolute rubric. There is no A/B comparison.
* **Arm bias** - a model told which patch came from the "improved" harness will find it
  improved. Avoided because the judge is never told which arm it is looking at, and the
  harness files are excluded from the patch it sees.
* **Outcome contamination** - a judge that knows the tests passed will justify a high
  score with that fact, turning two signals into one counted twice. Avoided structurally:
  test results are not in the prompt, and cannot be.

Reliability is measured rather than assumed: every patch is judged more than once, and
disagreement between those judgements is reported instead of averaged away.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

PROMPT_DIR = Path(__file__).parent / "prompts"
# Evidence must point at a line of the patch. Anything else is fluent but ungrounded.
EVIDENCE_RE = re.compile(r"[\w./\\-]+\.\w+:\d+")


class ProviderResponse(BaseModel):
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict | None = None


class LLMProvider(Protocol):
    """Anything that can turn a prompt into text.

    Deliberately tiny. Swapping Claude for another provider is one class, not a refactor,
    and the tool never depends on a provider-specific feature.
    """

    name: str

    def complete(
        self, system: str, user: str, *, model: str, max_tokens: int, temperature: float
    ) -> ProviderResponse: ...


class CriterionScore(BaseModel):
    score: int | None = None
    evidence: list[str] = Field(default_factory=list)
    discarded_reason: str = ""

    @property
    def scored(self) -> bool:
        return self.score is not None


class JudgeSample(BaseModel):
    """One independent judgement of one patch."""

    criteria: dict[str, CriterionScore] = Field(default_factory=dict)
    confidence: float | None = None
    reasoning: str = ""
    raw: str = ""
    error: str = ""


class JudgeResult(BaseModel):
    """The aggregate of several samples, with disagreement preserved rather than smoothed."""

    task_id: str = ""
    run_id: str = ""
    provider: str = ""
    model: str = ""
    prompt_version: str = "v1"
    prompt_sha256: str = ""
    samples: list[JudgeSample] = Field(default_factory=list)
    criteria: dict[str, float] = Field(default_factory=dict)
    score: float | None = None
    confidence: float | None = None
    low_agreement: bool = False
    disagreement: dict[str, int] = Field(default_factory=dict)
    error: str = ""


def parse_response(text: str) -> JudgeSample:
    """Parse and *validate* a judge reply.

    Validation is where the evidence rule is enforced. A criterion whose evidence does not
    cite a file and a line is discarded rather than scored, because an unevidenced score
    from a fluent model is indistinguishable from a confident guess.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", cleaned).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return JudgeSample(raw=text, error="no JSON object in response")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        return JudgeSample(raw=text, error=f"invalid JSON: {exc}")
    if not isinstance(payload, dict):
        return JudgeSample(raw=text, error="response JSON is not an object")

    criteria: dict[str, CriterionScore] = {}
    for name, value in (payload.get("criteria") or {}).items():
        if not isinstance(value, dict):
            continue
        evidence = [str(e) for e in (value.get("evidence") or []) if str(e).strip()]
        grounded = [e for e in evidence if EVIDENCE_RE.search(e)]
        raw_score = value.get("score")
        score = int(raw_score) if isinstance(raw_score, int | float) else None
        if score is not None and not 1 <= score <= 5:
            score = None
        if score is not None and not grounded:
            criteria[name] = CriterionScore(
                score=None, evidence=evidence,
                discarded_reason="no evidence citing a file and line in the patch",
            )
            continue
        criteria[name] = CriterionScore(score=score, evidence=evidence)

    confidence = payload.get("confidence")
    return JudgeSample(
        criteria=criteria,
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
        reasoning=str(payload.get("reasoning", "")), raw=text,
    )


class Judge:
    """Scores one patch against one rubric, several times."""

    def __init__(
        self, provider: LLMProvider, *, model: str, prompt_version: str = "v1",
        self_consistency: int = 2, temperature: float = 0.0, max_patch_chars: int = 24_000,
    ) -> None:
        self.provider = provider
        self.model = model
        self.prompt_version = prompt_version
        self.self_consistency = max(1, self_consistency)
        self.temperature = temperature
        self.max_patch_chars = max_patch_chars
        path = PROMPT_DIR / f"requirement_adherence.{prompt_version}.md"
        if not path.exists():
            raise FileNotFoundError(f"no judge prompt for version {prompt_version!r}: {path}")
        self.template = path.read_text()
        # Results from different prompt versions are never pooled, so the version travels
        # with every score all the way into the report.
        self.template_sha256 = hashlib.sha256(self.template.encode()).hexdigest()

    def build_prompt(self, *, task_prompt: str, patch: str, repo_context: str, rubric: str) -> str:
        if len(patch) > self.max_patch_chars:
            keep = self.max_patch_chars // 2
            patch = (
                patch[:keep]
                + f"\n\n... [{len(patch) - 2 * keep} characters of patch omitted] ...\n\n"
                + patch[-keep:]
            )
        return self.template.format(
            task_prompt=task_prompt.strip(),
            repo_context=repo_context.strip(),
            rubric=rubric.strip(),
            patch=patch.strip() or "(the patch is empty - the engineer changed nothing)",
        )

    def evaluate(
        self, *, task_prompt: str, patch: str, repo_context: str, rubric: str,
        task_id: str = "", run_id: str = "",
    ) -> JudgeResult:
        prompt = self.build_prompt(
            task_prompt=task_prompt, patch=patch, repo_context=repo_context, rubric=rubric
        )
        system = (
            "You are a meticulous senior engineer reviewing a patch against a rubric. "
            "You reply with JSON only."
        )

        samples: list[JudgeSample] = []
        for _ in range(self.self_consistency):
            try:
                response = self.provider.complete(
                    system, prompt, model=self.model, max_tokens=2000,
                    temperature=self.temperature,
                )
                samples.append(parse_response(response.text))
            except Exception as exc:  # noqa: BLE001 - a provider failure must not abort a run
                samples.append(JudgeSample(error=f"{type(exc).__name__}: {exc}"))

        return self._aggregate(
            samples, task_id=task_id, run_id=run_id,
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        )

    def _aggregate(
        self, samples: list[JudgeSample], *, task_id: str, run_id: str, prompt_sha256: str
    ) -> JudgeResult:
        """Average the samples, and flag criteria the judge could not agree with itself on.

        A criterion whose independent judgements differ by 2 or more points on a 5-point
        scale is not a measurement, it is a coin flip. Those are reported but excluded from
        the aggregate, so a noisy judge widens the review queue instead of quietly moving
        the verdict.
        """
        names: list[str] = []
        for sample in samples:
            for name in sample.criteria:
                if name not in names:
                    names.append(name)

        criteria: dict[str, float] = {}
        disagreement: dict[str, int] = {}
        for name in names:
            scores: list[int] = [
                value
                for s in samples
                if name in s.criteria and (value := s.criteria[name].score) is not None
            ]
            if not scores:
                continue
            spread = max(scores) - min(scores)
            disagreement[name] = spread
            if spread < 2:
                criteria[name] = sum(scores) / len(scores)

        low_agreement = any(spread >= 2 for spread in disagreement.values())
        confidences = [s.confidence for s in samples if s.confidence is not None]
        errors = [s.error for s in samples if s.error]

        return JudgeResult(
            task_id=task_id, run_id=run_id, provider=self.provider.name, model=self.model,
            prompt_version=self.prompt_version, prompt_sha256=prompt_sha256, samples=samples,
            criteria=criteria,
            score=sum(criteria.values()) / len(criteria) if criteria else None,
            confidence=sum(confidences) / len(confidences) if confidences else None,
            low_agreement=low_agreement, disagreement=disagreement,
            error="; ".join(errors) if errors and len(errors) == len(samples) else "",
        )

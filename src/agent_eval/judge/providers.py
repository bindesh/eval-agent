"""LLM providers.

Two exist: a real one and a deterministic one. The deterministic one is not a toy - it is
what lets the entire test suite exercise the judge pipeline, including its failure paths,
without a network call or an API key.
"""

from __future__ import annotations

import json
import os

from .base import LLMProvider, ProviderResponse


class AnthropicProvider(LLMProvider):
    """Calls the Anthropic Messages API.

    The SDK is an optional dependency (`pip install -e ".[judge]"`), so judging stays
    opt-in and the tool installs and runs without it.
    """

    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise RuntimeError(
                'the anthropic package is not installed; run: pip install -e ".[judge]"'
            ) from exc
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Judging sends your patch to a third-party "
                "model, so it is opt-in: run with --no-judge to skip it."
            )
        self._client = anthropic.Anthropic(api_key=key)

    def complete(
        self, system: str, user: str, *, model: str, max_tokens: int, temperature: float
    ) -> ProviderResponse:
        message = self._client.messages.create(
            model=model, max_tokens=max_tokens, temperature=temperature,
            system=system, messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return ProviderResponse(
            text=text, model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )


class MockProvider(LLMProvider):
    """A deterministic stand-in judge.

    It reads the patch it is shown and scores from simple, stated heuristics. It is not
    pretending to be a good judge; it is a fixture that makes the judge *pipeline*
    testable - prompt construction, JSON parsing, evidence validation, self-consistency,
    aggregation - with no network and no cost.

    `scripted_responses` lets a test drive an exact reply (malformed JSON, missing
    evidence, an out-of-range score) to exercise the validation paths. `jitter` shifts
    the score on alternate calls, which is how the self-consistency machinery gets
    tested: two judgements of the same patch that disagree.
    """

    name = "mock"

    def __init__(self, scripted_responses: list[str] | None = None, *, jitter: int = 0) -> None:
        self.scripted = list(scripted_responses or [])
        self.jitter = jitter
        self.calls: list[dict] = []

    def complete(
        self, system: str, user: str, *, model: str, max_tokens: int, temperature: float
    ) -> ProviderResponse:
        self.calls.append({"system": system, "user": user, "model": model})
        if self.scripted:
            return ProviderResponse(text=self.scripted.pop(0), model=model)

        patch = user.split("<patch>", 1)[-1].split("</patch>", 1)[0]
        empty = "changed nothing" in patch
        added = [ln for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        file_line = next(
            (ln.removeprefix("+++ b/") for ln in patch.splitlines() if ln.startswith("+++ b/")),
            "src/unknown.py",
        )

        if empty:
            score = 1
        elif len(added) > 4:
            score = 4
        else:
            score = 3
        # Alternate calls disagree by `jitter`, so a single patch judged twice can
        # produce the disagreement the aggregation is supposed to catch.
        if self.jitter and len(self.calls) % 2 == 0:
            score = max(1, min(5, score + self.jitter))
        second = score

        payload = {
            "criteria": {
                "requirement_completeness": {
                    "score": score,
                    "evidence": [f"{file_line}:1 - patch adds {len(added)} lines"],
                },
                "convention_adherence": {
                    "score": second,
                    "evidence": [f"{file_line}:1 - style of the added lines"],
                },
            },
            "confidence": 0.5,
            "reasoning": "Deterministic mock judgement derived from the patch size.",
        }
        return ProviderResponse(text=json.dumps(payload), model=model)


def build_provider(name: str, **kwargs) -> LLMProvider:
    if name == "anthropic":
        return AnthropicProvider(**kwargs)
    if name == "mock":
        return MockProvider()
    if name == "heuristic":
        return HeuristicProvider()
    raise ValueError(f"unknown judge provider: {name!r}")


class HeuristicProvider(LLMProvider):
    """A rule-based stand-in judge for the offline demo. **Not a language model.**

    The demo has to show the judged dimension and the measured-vs-judged disagreement
    matrix, because those are the parts of the design that matter most - and it has to do
    it without an API key. A random score would make that showcase meaningless, so this
    provider applies a handful of stated rules to the patch and emits the same evidenced
    JSON a real judge emits.

    It reports its model as ``heuristic-demo-1 (NOT an LLM)`` so nothing downstream, and
    no reader of the report, can mistake it for a model judgement. The rules:

    * an empty patch scores 1 everywhere;
    * raising a builtin exception in a codebase that ships its own error hierarchy is a
      convention failure;
    * a helper extracted as a module-level ``def`` is reusable by another module; one
      extracted as an indented ``def _name`` on the class is not;
    * widening a public return annotation to a container type suggests a broken contract;
    * adding a test file is evidence of maintainability.

    Every one of those is a crude proxy for something a real judge would reason about.
    That is the point of labelling it loudly rather than dressing it up.
    """

    name = "heuristic"
    MODEL = "heuristic-demo-1 (NOT an LLM)"

    def complete(
        self, system: str, user: str, *, model: str, max_tokens: int, temperature: float
    ) -> ProviderResponse:
        patch = user.split("<patch>", 1)[-1].split("</patch>", 1)[0]
        added = [
            line[1:] for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        files = [
            line.removeprefix("+++ b/") for line in patch.splitlines()
            if line.startswith("+++ b/")
        ]
        primary = files[0] if files else "src/unknown.py"
        body = "\n".join(added)

        completeness, convention, maintainability = 3, 3, 3
        evidence_c, evidence_v, evidence_m = [], [], []

        if "changed nothing" in patch or not added:
            payload = {
                "criteria": {
                    name: {"score": 1, "evidence": [f"{primary}:0 - the patch is empty"]}
                    for name in ("requirement_completeness", "convention_adherence",
                                 "maintainability")
                },
                "confidence": 0.9,
                "reasoning": "The patch changes nothing, so it cannot satisfy the task.",
            }
            return ProviderResponse(text=json.dumps(payload), model=self.MODEL)

        if len(added) >= 6:
            completeness += 1
            evidence_c.append(f"{primary}:1 - {len(added)} added lines cover the behaviour")
        else:
            evidence_c.append(f"{primary}:1 - only {len(added)} added lines")

        if "raise ValueError" in body or "raise KeyError" in body:
            convention = 2
            evidence_v.append(
                f"{primary}:1 - raises a builtin exception where the package defines its own"
            )
        elif "Error(" in body or "require_" in body:
            convention += 1
            evidence_v.append(f"{primary}:1 - reuses the package's own error/validation helpers")
        else:
            evidence_v.append(f"{primary}:1 - no error or validation convention signal")

        module_level = [ln for ln in added if ln.startswith("def ")]
        private_method = [
            ln for ln in added
            if ln.strip().startswith("def _") and ln.startswith(" ")
        ]
        if module_level:
            completeness += 1
            evidence_c.append(f"{primary}:1 - defines {module_level[0].strip()} at module level, "
                              f"so another module can import it")
        elif private_method:
            completeness -= 1
            evidence_c.append(f"{primary}:1 - {private_method[0].strip()} is private to the "
                              f"class, so a second implementation still cannot reuse it")

        if "-> dict[" in body or "-> dict:" in body:
            completeness -= 1
            evidence_c.append(f"{primary}:1 - widens a public return type to a container, "
                              f"which existing callers will not expect")

        if any(f.startswith("tests/") for f in files):
            maintainability += 1
            evidence_m.append(f"{files[-1]}:1 - adds a test file")
        else:
            evidence_m.append(f"{primary}:1 - no test file added in this patch")

        clamp = lambda v: max(1, min(5, v))  # noqa: E731
        payload = {
            "criteria": {
                "requirement_completeness": {"score": clamp(completeness), "evidence": evidence_c},
                "convention_adherence": {"score": clamp(convention), "evidence": evidence_v},
                "maintainability": {"score": clamp(maintainability), "evidence": evidence_m},
            },
            "confidence": 0.4,
            "reasoning": (
                "Rule-based demo judgement. Not a language model; these scores are proxies "
                "and should not be read as a model's opinion."
            ),
        }
        return ProviderResponse(text=json.dumps(payload), model=self.MODEL)

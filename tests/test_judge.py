"""The judge, and the structural guarantees that keep it from double-counting."""

import inspect
import json
import re

import pytest

from agent_eval.judge import Judge, MockProvider, parse_response
from agent_eval.judging import build_repo_context

PATCH = "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1,3 @@\n+def go():\n+    return 1\n+\n"


def judge(provider=None, **kwargs):
    return Judge(provider or MockProvider(), model="m", **kwargs)


# --- the structural guarantees ---------------------------------------------

def _sections(prompt):
    """The parts of the prompt filled with data, as opposed to the fixed instructions."""
    return {
        tag: re.search(rf"<{tag}>(.*?)</{tag}>", prompt, re.S).group(1)
        for tag in ("task", "context", "rubric", "patch")
    }


def test_no_outcome_information_can_reach_the_judge():
    """Not a prompt instruction - a structural guarantee, asserted two ways.

    A judge that knew the tests passed would agree with them by construction, and the
    measured and judged signals would collapse into one counted twice. The template does
    *mention* test results, in the rule telling the judge not to reason about them; what
    must never happen is an outcome arriving in the data sections, and there must be no
    API through which one could.
    """
    prompt = judge().build_prompt(
        task_prompt="Add CSV export", patch=PATCH,
        repo_context="# README\nA small service.", rubric="## completeness\nDoes it?",
    )
    forbidden = ("passed", "failed", "exit code", "pytest", "ruff", "mypy", "PASS", "FAIL")
    for tag, body in _sections(prompt).items():
        for word in forbidden:
            assert word.lower() not in body.lower(), f"outcome leaked into <{tag}>: {word}"

    # And there is no parameter through which a caller could supply one.
    parameters = set(inspect.signature(Judge.evaluate).parameters)
    assert parameters == {"self", "task_prompt", "patch", "repo_context", "rubric",
                          "task_id", "run_id"}


def test_the_prompt_never_reveals_which_arm_it_is():
    prompt = judge().build_prompt(
        task_prompt="t", patch=PATCH, repo_context="c", rubric="r"
    )
    lowered = prompt.lower()
    for forbidden in ("baseline", "candidate", "harness", "agents.md"):
        assert forbidden not in lowered, f"judge prompt leaks arm identity: {forbidden}"


def test_repo_context_excludes_harness_files(real_benchmark_dir):
    from agent_eval.benchmark import load_benchmark
    from agent_eval.models import RunRecord

    record = RunRecord(
        run_id="r", evaluation_id="e", task_id="t01-export-csv", arm="candidate", rep=1,
        order_index=0, harness_id="h", harness_short_id="hns_x",
        changed_files=["src/customers/service.py"],
    )
    context = build_repo_context(load_benchmark(real_benchmark_dir), record)
    assert "service.py" in context
    assert "AGENTS.md" not in context
    assert "repo-conventions" not in context


def test_an_empty_patch_is_described_as_such():
    prompt = judge().build_prompt(task_prompt="t", patch="", repo_context="c", rubric="r")
    assert "changed nothing" in prompt


def test_a_huge_patch_is_truncated_in_the_middle():
    prompt = judge(max_patch_chars=400).build_prompt(
        task_prompt="t", patch="x" * 5000, repo_context="c", rubric="r"
    )
    assert "characters of patch omitted" in prompt
    assert len(prompt) < 3000


# --- response validation ----------------------------------------------------

def test_scores_need_evidence_citing_a_file_and_line():
    sample = parse_response(json.dumps({
        "criteria": {"a": {"score": 5, "evidence": ["it is clearly excellent"]}},
    }))
    assert sample.criteria["a"].score is None
    assert "no evidence" in sample.criteria["a"].discarded_reason


def test_grounded_evidence_is_accepted():
    sample = parse_response(json.dumps({
        "criteria": {"a": {"score": 4, "evidence": ["src/x.py:12 - adds the exporter"]}},
    }))
    assert sample.criteria["a"].score == 4


def test_out_of_range_scores_are_rejected():
    sample = parse_response(json.dumps({
        "criteria": {"a": {"score": 11, "evidence": ["src/x.py:1 - x"]}},
    }))
    assert sample.criteria["a"].score is None


def test_a_fenced_json_block_is_accepted():
    sample = parse_response(
        '```json\n{"criteria": {"a": {"score": 3, "evidence": ["src/x.py:1 - x"]}}}\n```'
    )
    assert sample.criteria["a"].score == 3


def test_prose_around_the_json_is_tolerated():
    sample = parse_response(
        'Sure! {"criteria": {"a": {"score": 3, "evidence": ["src/x.py:1 - x"]}}} Hope that helps.'
    )
    assert sample.criteria["a"].score == 3


def test_unparseable_output_is_an_error_not_a_score():
    assert parse_response("I think it looks fine, honestly.").error


# --- self-consistency -------------------------------------------------------

def test_agreeing_judgements_are_averaged():
    result = judge(self_consistency=2).evaluate(
        task_prompt="t", patch=PATCH, repo_context="c", rubric="r"
    )
    assert result.score is not None
    assert not result.low_agreement


def test_disagreeing_judgements_are_excluded_and_flagged():
    """A criterion whose independent judgements differ by 2+ points on a 5-point scale is
    a coin flip, not a measurement. It widens the review queue instead of moving the verdict."""
    result = judge(MockProvider(jitter=-3), self_consistency=2).evaluate(
        task_prompt="t", patch=PATCH * 3, repo_context="c", rubric="r"
    )
    assert result.low_agreement
    assert max(result.disagreement.values()) >= 2
    assert result.criteria == {}, "disagreed criteria must not reach the aggregate"


def test_a_provider_failure_does_not_abort_the_run():
    class Broken:
        name = "broken"

        def complete(self, *a, **k):
            raise RuntimeError("upstream is down")

    result = judge(Broken(), self_consistency=2).evaluate(
        task_prompt="t", patch=PATCH, repo_context="c", rubric="r"
    )
    assert result.score is None and "upstream is down" in result.error


def test_provenance_travels_with_every_score():
    """Results from different judge prompts or models are not comparable and must never
    be pooled, so the version and the model id ride along with the number."""
    result = judge(prompt_version="v1").evaluate(
        task_prompt="t", patch=PATCH, repo_context="c", rubric="r", task_id="t1"
    )
    assert result.prompt_version == "v1" and result.model == "m"
    assert result.provider == "mock" and result.prompt_sha256


def test_an_unknown_prompt_version_fails_loudly():
    with pytest.raises(FileNotFoundError, match="no judge prompt"):
        Judge(MockProvider(), model="m", prompt_version="v99")


def test_the_heuristic_demo_provider_labels_itself_as_not_a_model():
    from agent_eval.judge import HeuristicProvider

    response = HeuristicProvider().complete(
        "s", f"<patch>{PATCH}</patch>", model="ignored", max_tokens=10, temperature=0
    )
    assert "NOT an LLM" in response.model


# --- judging through the CLI you are already logged in to -------------------

def test_claude_code_provider_reports_a_missing_cli_clearly():
    from agent_eval.judge import ClaudeCodeProvider

    with pytest.raises(RuntimeError, match="needs the .* CLI on PATH"):
        ClaudeCodeProvider(executable="definitely-not-a-real-binary-xyz")


def test_claude_code_provider_unwraps_the_cli_envelope(monkeypatch, tmp_path):
    """The CLI wraps the reply in its own JSON; the judge's JSON is in `result`."""
    import subprocess

    from agent_eval.judge import ClaudeCodeProvider

    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    provider = ClaudeCodeProvider(executable=str(fake))

    inner = '{"criteria": {"a": {"score": 4, "evidence": ["src/x.py:1 - adds it"]}}}'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0,
        stdout=json.dumps({"result": inner, "model": "claude-sonnet-4-5",
                           "usage": {"input_tokens": 900, "output_tokens": 40}}),
        stderr="",
    ))
    response = provider.complete("sys", "user", model="m", max_tokens=100, temperature=0)
    assert response.text == inner
    assert response.input_tokens == 900
    # and it parses through the normal judge path
    assert parse_response(response.text).criteria["a"].score == 4


def test_claude_code_provider_surfaces_a_cli_error(monkeypatch, tmp_path):
    """An expired login must raise, not return an empty judgement that scores as a 1."""
    import subprocess

    from agent_eval.judge import ClaudeCodeProvider

    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    provider = ClaudeCodeProvider(executable=str(fake))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0,
        stdout=json.dumps({"is_error": True, "result": "OAuth session expired"}), stderr="",
    ))
    with pytest.raises(RuntimeError, match="OAuth session expired"):
        provider.complete("sys", "user", model="m", max_tokens=100, temperature=0)

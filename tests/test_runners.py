import json

import pytest

from agent_eval.runners import (
    AgentRunRequest,
    AgentRunResult,
    CassetteNotFound,
    ClaudeCodeRunner,
    ReplayRunner,
    SimulatedRunner,
)
from agent_eval.runners.replay import cassette_name


def _request(tmp_path, **kwargs):
    defaults = dict(
        run_id="r1", workspace=tmp_path, prompt="do the thing", model="m",
        task_id="t01-export-csv", harness_id="a" * 64, arm="baseline", rep=1,
    )
    return AgentRunRequest(**{**defaults, **kwargs})


# --- claude-code adapter ----------------------------------------------------

def test_argv_includes_model_and_json_output(tmp_path):
    argv = ClaudeCodeRunner().build_argv(_request(tmp_path))
    assert argv[:3] == ["claude", "-p", "do the thing"]
    assert "--output-format" in argv and "json" in argv
    assert argv[argv.index("--model") + 1] == "m"


def test_argv_carries_extra_args(tmp_path):
    argv = ClaudeCodeRunner(extra_args=["--verbose"]).build_argv(_request(tmp_path))
    assert argv[-1] == "--verbose"


def test_usage_is_parsed_as_actual_when_the_cli_reports_it():
    payload = json.dumps({
        "total_cost_usd": 0.1234, "num_turns": 7, "model": "claude-sonnet-4-5",
        "session_id": "s1",
        "usage": {"input_tokens": 41000, "output_tokens": 1500,
                  "cache_read_input_tokens": 900},
    })
    _, usage, model, session = ClaudeCodeRunner.parse_payload(payload)
    assert usage.source == "actual"
    assert usage.cost_usd == 0.1234 and usage.turns == 7
    assert usage.total_tokens == 42500
    assert (model, session) == ("claude-sonnet-4-5", "s1")


def test_missing_usage_is_unavailable_not_zero():
    """A fabricated zero in a report about cost is worse than no number at all."""
    _, usage, _, _ = ClaudeCodeRunner.parse_payload("plain text, not json")
    assert usage.source == "unavailable"
    assert usage.cost_usd is None and usage.total_tokens is None


def test_partial_payload_degrades_field_by_field():
    _, usage, _, _ = ClaudeCodeRunner.parse_payload(json.dumps({"num_turns": 3}))
    assert usage.source == "actual" and usage.turns == 3
    assert usage.cost_usd is None


def test_json_that_is_not_an_object_is_unavailable():
    _, usage, _, _ = ClaudeCodeRunner.parse_payload("[1, 2, 3]")
    assert usage.source == "unavailable"


def test_missing_executable_raises_a_useful_error(tmp_path):
    runner = ClaudeCodeRunner(executable="definitely-not-a-real-binary-xyz")
    assert not runner.available()
    with pytest.raises(RuntimeError, match="--agent simulated"):
        runner.run(_request(tmp_path))


# --- replay adapter ---------------------------------------------------------

def test_cassette_key_is_content_addressed():
    """Keyed on the harness content hash, so editing a harness cannot silently replay a
    recording made under the old one."""
    a = cassette_name("t1", "a" * 64, 1)
    b = cassette_name("t1", "b" * 64, 1)
    assert a != b and a.startswith("t1__") and a.endswith("rep01.json")


def test_record_then_replay_reapplies_the_patch(tmp_path):
    from agent_eval.workspace import create_workspace

    from .conftest import write
    fixture = tmp_path / "fx"
    write(fixture / "src" / "app.py", "VALUE = 1\n")
    source = create_workspace(fixture, tmp_path / "source")
    (source.path / "src" / "app.py").write_text("VALUE = 2\n")
    patch = source.diff()

    runner = ReplayRunner(tmp_path / "cassettes")
    runner.record(
        _request(source.path), AgentRunResult(adapter="claude-code", duration_s=42.0), patch
    )

    target = create_workspace(fixture, tmp_path / "target")
    result = runner.run(_request(target.path))
    assert (target.path / "src" / "app.py").read_text() == "VALUE = 2\n"
    assert result.duration_s == 42.0
    assert "replay" in result.adapter


def test_replay_without_a_cassette_is_loud(tmp_path):
    with pytest.raises(CassetteNotFound, match="no recording"):
        ReplayRunner(tmp_path).run(_request(tmp_path))


def test_replay_can_be_lenient_when_asked(tmp_path):
    result = ReplayRunner(tmp_path, strict=False).run(_request(tmp_path))
    assert result.exit_code == 1 and "no cassette" in result.note


def test_replay_refuses_a_cassette_that_no_longer_applies(tmp_path):
    (tmp_path / cassette_name("t01-export-csv", "a" * 64, 1)).write_text(json.dumps({
        "result": AgentRunResult(adapter="claude-code").model_dump(mode="json"),
        "patch": "--- a/nope.py\n+++ b/nope.py\n@@ -1 +1 @@\n-x\n+y\n",
    }))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(CassetteNotFound, match="no longer applies"):
        ReplayRunner(tmp_path).run(_request(workspace))


# --- simulated adapter ------------------------------------------------------

def test_simulated_is_deterministic_for_the_same_key(tmp_path, real_benchmark_dir):
    runner = SimulatedRunner(real_benchmark_dir, seed=7)
    first = runner.run(_request(tmp_path / "a", arm="baseline", rep=1))
    (tmp_path / "b").mkdir(parents=True, exist_ok=True)
    second = runner.run(_request(tmp_path / "b", arm="baseline", rep=1))
    assert first.raw["attempt"] == second.raw["attempt"]
    assert first.duration_s == second.duration_s


def test_simulated_repetitions_differ(tmp_path, real_benchmark_dir):
    """If every repetition produced the same thing, repeating runs would measure nothing."""
    runner = SimulatedRunner(real_benchmark_dir, seed=7)
    durations = set()
    for rep in (1, 2, 3):
        directory = tmp_path / f"r{rep}"
        directory.mkdir()
        durations.add(runner.run(_request(directory, rep=rep)).duration_s)
    assert len(durations) > 1


def test_simulated_usage_is_labelled_simulated(tmp_path, real_benchmark_dir):
    result = SimulatedRunner(real_benchmark_dir).run(_request(tmp_path))
    assert result.usage.source == "simulated"
    assert result.simulated is True


def test_simulated_candidate_arm_costs_more(tmp_path, real_benchmark_dir):
    """The benchmark declares that the candidate harness reads more context."""
    runner = SimulatedRunner(real_benchmark_dir, seed=1)
    base_dir, cand_dir = tmp_path / "b", tmp_path / "c"
    base_dir.mkdir()
    cand_dir.mkdir()
    base = runner.run(_request(base_dir, arm="baseline", rep=1))
    cand = runner.run(_request(cand_dir, arm="candidate", rep=1))
    assert cand.usage.cost_usd > base.usage.cost_usd


def test_simulated_writes_real_files(tmp_path, real_benchmark_dir):
    result = SimulatedRunner(real_benchmark_dir, seed=3).run(
        _request(tmp_path, task_id="t02-fix-lookup-bug")
    )
    assert result.raw["files"], "the simulated agent must produce a real patch"
    assert (tmp_path / result.raw["files"][0]).exists()


def test_simulated_reports_when_no_attempt_is_defined(tmp_path, real_benchmark_dir):
    result = SimulatedRunner(real_benchmark_dir).run(_request(tmp_path, arm="nonexistent-arm"))
    assert result.exit_code == 1 and "no simulated attempt" in result.note


# --- the harness owns its own agent configuration ---------------------------

def test_harness_permission_mode_reaches_the_command_line(tmp_path):
    """Regression test. `permission_mode` is declared in harness.yaml and is part of the
    harness content hash, so changing it IS a harness change and appears in the report's
    diff. It must therefore actually be applied - otherwise the tool would report a
    harness as changed and then evaluate it as though it had not."""
    request = _request(tmp_path, config={"permission_mode": "plan"})
    argv = ClaudeCodeRunner(permission_mode="acceptEdits").build_argv(request)
    assert argv[argv.index("--permission-mode") + 1] == "plan"


def test_harness_extra_args_reach_the_command_line(tmp_path):
    request = _request(tmp_path, config={"extra_args": ["--verbose", "--foo"]})
    argv = ClaudeCodeRunner().build_argv(request)
    assert argv[-2:] == ["--verbose", "--foo"]


def test_constructor_values_are_only_a_fallback(tmp_path):
    argv = ClaudeCodeRunner(
        permission_mode="acceptEdits", extra_args=["--fallback"]
    ).build_argv(_request(tmp_path, config={}))
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[-1] == "--fallback"


def test_a_tilde_in_the_executable_path_is_expanded():
    """The native installer puts the launcher at ~/.local/bin/claude, so configs name it
    that way. subprocess does not expand `~`, and the resulting "not found" error points
    at the wrong problem."""
    runner = ClaudeCodeRunner(executable="~/.local/bin/claude")
    assert not runner.executable.startswith("~")
    assert runner.executable.endswith("/.local/bin/claude")

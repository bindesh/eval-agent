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

def test_argv_streams_json_with_the_verbose_flag_the_cli_requires(tmp_path):
    argv = ClaudeCodeRunner().build_argv(_request(tmp_path))
    assert argv[:3] == ["claude", "-p", "do the thing"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv.count("--verbose") == 1
    assert argv[argv.index("--model") + 1] == "m"


def test_verbose_is_not_duplicated_when_the_harness_already_passes_it(tmp_path):
    argv = ClaudeCodeRunner(extra_args=["--verbose"]).build_argv(_request(tmp_path))
    assert argv.count("--verbose") == 1


def test_plain_json_output_is_still_supported(tmp_path):
    argv = ClaudeCodeRunner(output_format="json").build_argv(_request(tmp_path))
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--verbose" not in argv


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


# --- a run that never happened is not a run that failed ---------------------

EXPIRED_LOGIN = json.dumps({
    "is_error": True, "terminal_reason": "api_error", "num_turns": 1,
    "result": "Failed to authenticate: OAuth session expired and could not be refreshed",
    "total_cost_usd": 0,
    "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0},
})


def test_an_expired_login_is_reported_as_an_infrastructure_error():
    """Observed in the wild. The CLI exits 1 in under a second with a usage block full of
    zeros. Treated naively that is indistinguishable from 'the agent wrote nothing', which
    scores as a task failure and turns an expired credential into evidence about the
    harness."""
    payload, _, _, _ = ClaudeCodeRunner.parse_payload(EXPIRED_LOGIN)
    error = ClaudeCodeRunner.infrastructure_error(payload, 1)
    assert "OAuth session expired" in error


def test_zeros_from_a_failed_run_are_never_labelled_actual():
    """The README promises a fabricated zero is never reported as measured. This is the
    case that broke that promise: cost 0.0, source 'actual', from a run that never ran."""
    _, usage, _, _ = ClaudeCodeRunner.parse_payload(EXPIRED_LOGIN)
    assert usage.source == "unavailable"
    assert usage.cost_usd is None
    assert usage.total_tokens is None


def test_a_genuine_zero_cost_run_is_still_actual():
    """Only zeros accompanied by an error are suppressed - a real run that happens to
    report no cost keeps its measurement."""
    payload = json.dumps({
        "total_cost_usd": 0.0, "num_turns": 3,
        "usage": {"input_tokens": 1200, "output_tokens": 50},
    })
    _, usage, _, _ = ClaudeCodeRunner.parse_payload(payload)
    assert usage.source == "actual" and usage.total_tokens == 1250


def test_a_successful_payload_has_no_infrastructure_error():
    payload, _, _, _ = ClaudeCodeRunner.parse_payload(
        json.dumps({"total_cost_usd": 0.12, "usage": {"input_tokens": 100}})
    )
    assert ClaudeCodeRunner.infrastructure_error(payload, 0) == ""


def test_unparseable_output_with_a_nonzero_exit_is_an_infrastructure_error():
    assert "exited 127" in ClaudeCodeRunner.infrastructure_error(None, 127)


# --- streaming: live activity without changing what is measured -------------

def _stream(workspace, *, result=True):
    events = [
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5", "session_id": "s1"},
        {"type": "assistant", "message": {"id": "m1", "content": [{"type": "thinking"}]}},
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "name": "Read",
             "input": {"file_path": f"{workspace}/src/customers/service.py"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "..."}]}},
        {"type": "assistant", "message": {"id": "m2", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q\necho done"}}]}},
    ]
    if result:
        events.append({
            "type": "result", "is_error": False, "num_turns": 2, "session_id": "s1",
            "total_cost_usd": 0.05, "usage": {"input_tokens": 10, "output_tokens": 5},
        })
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_a_stream_yields_the_same_usage_as_the_single_json_payload(tmp_path):
    payload, usage, model, session = ClaudeCodeRunner.parse_payload(_stream(tmp_path))
    assert usage.source == "actual" and usage.cost_usd == 0.05 and usage.turns == 2
    assert model == "claude-sonnet-5"  # only the init event names it
    assert session == "s1"
    assert ClaudeCodeRunner.infrastructure_error(payload, 0) == ""


def test_a_stream_cut_off_before_its_result_has_no_usage(tmp_path):
    """A killed run printed turns but never a result. That is not a free run."""
    payload, usage, _, _ = ClaudeCodeRunner.parse_payload(_stream(tmp_path, result=False))
    assert payload is None
    assert usage.source == "unavailable"
    assert "without returning a parseable result" in ClaudeCodeRunner.infrastructure_error(
        payload, 1
    )


def _fake_claude(tmp_path, body):
    script = tmp_path / "fake-claude"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys, time\n"
        "if '--version' in sys.argv:\n    print('0.0.0 (fake)')\n    sys.exit(0)\n" + body
    )
    script.chmod(0o755)
    return str(script)


def test_activity_is_reported_while_the_agent_runs(tmp_path):
    stream = _stream(tmp_path)
    exe = _fake_claude(tmp_path, f"sys.stdout.write({stream!r}); sys.stdout.flush()\n")
    seen = []
    result = ClaudeCodeRunner(executable=exe).run(
        _request(tmp_path, on_activity=seen.append, timeout_seconds=30)
    )
    summaries = [(a.turn, a.summary) for a in seen]
    assert summaries == [
        (0, "agent started"), (1, "thinking"), (1, "Read src/customers/service.py"),
        (2, "Bash $ pytest -q"), (2, "agent finished"),
    ]
    assert result.usage.cost_usd == 0.05
    assert result.stdout == stream  # the whole stream is kept as the run's transcript


def test_a_broken_progress_display_never_fails_the_run(tmp_path):
    exe = _fake_claude(tmp_path, f"sys.stdout.write({_stream(tmp_path)!r})\n")

    def explode(_activity):
        raise RuntimeError("display bug")

    result = ClaudeCodeRunner(executable=exe).run(
        _request(tmp_path, on_activity=explode, timeout_seconds=30)
    )
    assert result.exit_code == 0 and result.usage.source == "actual"


def test_a_timeout_keeps_the_partial_stream(tmp_path):
    exe = _fake_claude(
        tmp_path, "print('{\"type\": \"system\", \"subtype\": \"init\"}', flush=True)\n"
        "time.sleep(30)\n",
    )
    result = ClaudeCodeRunner(executable=exe).run(_request(tmp_path, timeout_seconds=1))
    assert result.timed_out and result.exit_code is None
    assert '"init"' in result.stdout
    assert result.usage.source == "unavailable"


def test_the_progress_hook_is_not_part_of_the_serialised_request(tmp_path):
    request = _request(tmp_path, on_activity=print)
    assert "on_activity" not in request.model_dump()

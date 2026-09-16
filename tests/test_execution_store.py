"""The run pipeline end to end, plus artifact storage and redaction."""

import json
import sqlite3

from agent_eval.benchmark import load_benchmark
from agent_eval.execution import build_plan, execute_run
from agent_eval.harness import snapshot_harness
from agent_eval.redact import redact, safe_env_snapshot
from agent_eval.runners.base import AgentRunner, AgentRunRequest, AgentRunResult, AgentUsage
from agent_eval.store import EvaluationStore, rebuild_index

from .conftest import write


class ScriptedRunner(AgentRunner):
    """A programmable fake agent. Lets a test create an exact situation — a patch that
    passes, one that fails, a timeout, an agent that edits the test suite."""

    name = "scripted"

    def __init__(self, writes=None, *, delete=None, duration=12.5, exit_code=0,
                 timed_out=False, secret=""):
        self.writes = writes or {}
        self.delete = delete or []
        self.duration = duration
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.secret = secret

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        for rel, text in self.writes.items():
            target = request.workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        for rel in self.delete:
            (request.workspace / rel).unlink(missing_ok=True)
        return AgentRunResult(
            adapter=self.name, exit_code=self.exit_code, timed_out=self.timed_out,
            duration_s=self.duration, stdout=f"working... {self.secret}",
            usage=AgentUsage(source="actual", input_tokens=10, output_tokens=2, cost_usd=0.01),
        )


def _mini(tmp_path, mini_benchmark):
    benchmark = load_benchmark(mini_benchmark())
    harness_dir = tmp_path / "harness"
    write(harness_dir / "AGENTS.md", "# be good\n")
    write(harness_dir / "harness.yaml", "name: h\nagent: claude-code\nmodel: m\n")
    store = EvaluationStore(tmp_path / "runs", "eval-test").create()
    store.write_metadata(config={}, baseline=snapshot_harness(harness_dir),
                         candidate=snapshot_harness(harness_dir), extra={})
    return benchmark, snapshot_harness(harness_dir), store


SOLVED = {"src/thing.py": "def add(a, b):\n    return a + b\n"}


def test_a_correct_patch_passes_and_is_recorded(tmp_path, mini_benchmark):
    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    record = execute_run(
        benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
        item=build_plan(["t1"], 1)[0], runner=ScriptedRunner(SOLVED), store=store, model="m",
    )
    assert record.correctness_passed
    assert record.changed_files == ["src/thing.py"]
    assert record.diff_stat.insertions >= 1
    assert record.usage.source == "actual"


def test_the_harness_never_appears_in_the_patch(tmp_path, mini_benchmark):
    """Harness files are overlaid into the workspace and must be invisible to the diff,
    to changed_files, and therefore to the judge."""
    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    record = execute_run(
        benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
        item=build_plan(["t1"], 1)[0], runner=ScriptedRunner(SOLVED), store=store, model="m",
    )
    assert "AGENTS.md" not in record.changed_files
    assert "AGENTS.md" not in store.read_diff(record)


def test_agent_eval_config_is_not_left_in_the_agents_workspace(tmp_path, mini_benchmark):
    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    record = execute_run(
        benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
        item=build_plan(["t1"], 1)[0], runner=ScriptedRunner(SOLVED), store=store, model="m",
        keep_workspace=True,
    )
    from pathlib import Path
    workspace = Path(record.artifacts["workspace"])
    assert (workspace / "AGENTS.md").exists()
    assert not (workspace / "harness.yaml").exists()


def test_editing_the_protected_suite_is_detected_and_undone(tmp_path, mini_benchmark):
    """An agent that makes the tests pass by editing the tests is a real failure mode.
    It must be neither rewarded nor silently discarded."""
    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    benchmark.tasks[0].protected_paths = ["src/**"]
    record = execute_run(
        benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
        item=build_plan(["t1"], 1)[0],
        runner=ScriptedRunner({"src/thing.py": "def add(a, b):\n    return a + b\n"}),
        store=store, model="m",
    )
    assert record.tamper.detected
    assert record.tamper.paths == ["src/thing.py"]
    assert not record.correctness_passed, "the restored file must be what gets verified"


def test_a_failing_patch_keeps_its_evidence(tmp_path, mini_benchmark):
    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    record = execute_run(
        benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
        item=build_plan(["t1"], 1)[0],
        runner=ScriptedRunner({"src/thing.py": "def add(a, b):\n    return 7\n"}),
        store=store, model="m",
    )
    assert not record.correctness_passed
    assert "test_add" in (store.dir / record.artifacts["checks"]).read_text()


def test_secrets_are_redacted_before_they_are_written(tmp_path, mini_benchmark):
    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    secret = "sk-ant-" + "A" * 40
    record = execute_run(
        benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
        item=build_plan(["t1"], 1)[0], runner=ScriptedRunner(SOLVED, secret=secret),
        store=store, model="m",
    )
    stdout = (store.dir / record.artifacts["stdout"]).read_text()
    assert secret not in stdout and "[REDACTED]" in stdout


def test_runs_round_trip_through_disk(tmp_path, mini_benchmark):
    """Every later stage reads runs back from disk, so the round trip must be lossless."""
    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    for item in build_plan(["t1"], 2):
        execute_run(benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
                    item=item, runner=ScriptedRunner(SOLVED), store=store, model="m")
    loaded = store.read_runs()
    assert len(loaded) == 4
    assert {r.arm for r in loaded} == {"baseline", "candidate"}
    assert all(r.correctness_passed for r in loaded)


def test_the_index_is_rebuildable_from_the_files(tmp_path, mini_benchmark):
    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    for item in build_plan(["t1"], 1):
        execute_run(benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
                    item=item, runner=ScriptedRunner(SOLVED), store=store, model="m")
    assert rebuild_index(tmp_path / "runs") == 2
    connection = sqlite3.connect(tmp_path / "runs" / "index.sqlite")
    assert connection.execute("select count(*) from runs").fetchone()[0] == 2
    # Deleting it loses nothing; it is a cache, not the source of truth.
    (tmp_path / "runs" / "index.sqlite").unlink()
    assert rebuild_index(tmp_path / "runs") == 2


def test_metadata_records_the_harnesses_and_the_environment(tmp_path, mini_benchmark):
    _, _, store = _mini(tmp_path, mini_benchmark)
    metadata = json.loads((store.dir / "metadata.json").read_text())
    assert metadata["harnesses"]["baseline"]["harness_id"]
    assert metadata["environment"]["tool_version"]
    assert metadata["environment"]["python"]


# --- redaction --------------------------------------------------------------

def test_redaction_covers_common_credential_shapes():
    for secret in ["sk-ant-" + "x" * 40, "ghp_" + "y" * 36, "AKIA" + "Z" * 16,
                   "Bearer abcdef0123456789abcdef", "api_key = hunter2hunter2"]:
        assert secret not in redact(f"log line {secret} end")


def test_redaction_leaves_ordinary_text_alone():
    text = "ran pytest, 6 passed in 0.03s"
    assert redact(text) == text


def test_environment_snapshot_is_an_allow_list():
    """A full environment dump is the most reliable way to leak a credential."""
    snapshot = safe_env_snapshot()
    assert set(snapshot) <= {"PATH", "SHELL", "LANG", "TERM", "CI", "VIRTUAL_ENV",
                             "_secret_env_names"}


def test_the_harness_model_wins_over_the_evaluation_default(tmp_path, mini_benchmark):
    """`model` lives in harness.yaml and is part of the harness hash, so a harness that
    names a model must be run with it. Recording one model while running another would
    make every provenance claim in the report false."""
    class Recorder(ScriptedRunner):
        seen: str | None = None

        def run(self, request):
            Recorder.seen = request.model
            return super().run(request)

    benchmark = load_benchmark(mini_benchmark())
    harness_dir = tmp_path / "harness"
    write(harness_dir / "AGENTS.md", "# be good\n")
    write(harness_dir / "harness.yaml", "name: h\nagent: claude-code\nmodel: from-harness\n")
    store = EvaluationStore(tmp_path / "runs", "eval-model").create()
    store.write_metadata(config={}, baseline=snapshot_harness(harness_dir),
                         candidate=snapshot_harness(harness_dir), extra={})

    record = execute_run(
        benchmark=benchmark, task=benchmark.tasks[0], harness=snapshot_harness(harness_dir),
        item=build_plan(["t1"], 1)[0], runner=Recorder(SOLVED), store=store,
        model="from-the-command-line",
    )
    assert Recorder.seen == "from-harness"
    assert record.model == "from-harness"


def test_agent_runs_honour_benchmark_workspace_excludes(tmp_path, mini_benchmark):
    """Found in review: removing the kwarg at the execute_run call site broke no test."""
    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    benchmark = benchmark.model_copy(update={"workspace_excludes": ["target/"]})
    writes = {**SOLVED, "target/debug/app": "binary"}
    record = execute_run(
        benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
        item=build_plan(["t1"], 1)[0], runner=ScriptedRunner(writes), store=store, model="m",
    )
    assert record.changed_files == ["src/thing.py"]
    assert "target/" not in store.read_diff(record)


def test_progress_hooks_see_the_phases_and_the_agent_activity(tmp_path, mini_benchmark):
    from agent_eval.runners import AgentActivity

    class Chatty(ScriptedRunner):
        def run(self, request):
            request.on_activity(AgentActivity(turn=1, summary="Edit src/thing.py"))
            return super().run(request)

    benchmark, harness, store = _mini(tmp_path, mini_benchmark)
    seen = []
    record = execute_run(
        benchmark=benchmark, task=benchmark.tasks[0], harness=harness,
        item=build_plan(["t1"], 1)[0], runner=Chatty(SOLVED), store=store, model="m",
        on_activity=lambda a: seen.append(a.summary), on_phase=seen.append,
    )
    assert seen == [
        "preparing workspace", "waiting for the agent", "Edit src/thing.py", "running checks",
    ]
    assert record.correctness_passed

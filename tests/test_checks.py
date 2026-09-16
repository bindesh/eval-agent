import sys
from pathlib import Path

from agent_eval.checks import run_check, run_checks
from agent_eval.checks.runner import MAX_CAPTURE, _truncate
from agent_eval.models import CheckSpec
from agent_eval.workspace import create_workspace

from .conftest import write


def _ws(tmp_path: Path, body: str = "def test_ok():\n    assert True\n"):
    fixture = tmp_path / "fx"
    write(fixture / "pyproject.toml", "[tool.pytest.ini_options]\n")
    write(fixture / "verify" / "test_x.py", body)
    return create_workspace(fixture, tmp_path / "ws")


def test_pytest_check_passes(tmp_path):
    ws = _ws(tmp_path)
    out = run_check(CheckSpec(id="tests", type="pytest", args=["-q", "verify/"]), ws,
                    python=sys.executable)
    assert out.passed and out.exit_code == 0 and out.summary == "PASS"


def test_pytest_check_fails_and_keeps_evidence(tmp_path):
    ws = _ws(tmp_path, "def test_bad():\n    assert 1 == 2\n")
    out = run_check(CheckSpec(id="tests", type="pytest", args=["-q", "verify/"]), ws,
                    python=sys.executable)
    assert not out.passed
    assert "test_bad" in out.stdout, "raw failure output must survive for inspection"


def test_command_check_substitutes_python_token(tmp_path):
    ws = _ws(tmp_path)
    spec = CheckSpec(id="v", type="command", cmd=["{python}", "-c", "print('hi')"])
    out = run_check(spec, ws, python=sys.executable)
    assert out.passed
    assert out.command[0] == sys.executable, "{python} must resolve to the given interpreter"
    assert "hi" in out.stdout


def test_command_check_reports_nonzero_exit(tmp_path):
    ws = _ws(tmp_path)
    spec = CheckSpec(id="v", type="command", cmd=["{python}", "-c", "raise SystemExit(3)"])
    out = run_check(spec, ws, python=sys.executable)
    assert not out.passed and out.exit_code == 3


def test_command_check_times_out(tmp_path):
    ws = _ws(tmp_path)
    spec = CheckSpec(id="slow", type="command", timeout_seconds=1,
                     cmd=["{python}", "-c", "import time; time.sleep(30)"])
    out = run_check(spec, ws, python=sys.executable)
    assert out.timed_out and not out.passed and out.exit_code is None
    assert out.summary == "TIMEOUT"


def test_non_empty_diff_fails_on_an_untouched_workspace(tmp_path):
    ws = _ws(tmp_path)
    out = run_check(CheckSpec(id="changed", type="non_empty_diff", paths=["verify/"]), ws)
    assert not out.passed


def test_non_empty_diff_passes_once_something_changed(tmp_path):
    ws = _ws(tmp_path)
    (ws.path / "verify" / "test_x.py").write_text("def test_ok():\n    assert 1\n")
    out = run_check(CheckSpec(id="changed", type="non_empty_diff", paths=["verify/"]), ws)
    assert out.passed


def test_all_checks_run_even_after_an_early_failure(tmp_path):
    ws = _ws(tmp_path, "def test_bad():\n    assert 0\n")
    outs = run_checks([
        CheckSpec(id="tests", type="pytest", args=["-q", "verify/"]),
        CheckSpec(id="after", type="command", cmd=["{python}", "-c", "print(1)"]),
    ], ws, python=sys.executable)
    assert [o.id for o in outs] == ["tests", "after"]
    assert [o.passed for o in outs] == [False, True]


def test_long_output_is_truncated_in_the_middle():
    text = "a" * (MAX_CAPTURE + 5000)
    out = _truncate(text)
    assert len(out) < len(text)
    assert "omitted by agent-eval" in out
    assert out.startswith("a") and out.endswith("a")

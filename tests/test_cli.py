from typer.testing import CliRunner

from agent_eval.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "agent-eval" in result.stdout


def test_tasks_lists_the_benchmark(real_benchmark_dir):
    result = runner.invoke(app, ["tasks", "-b", str(real_benchmark_dir)])
    assert result.exit_code == 0
    assert "t01-export-csv" in result.stdout


def test_tasks_reports_a_bad_benchmark_path():
    result = runner.invoke(app, ["tasks", "-b", "/nonexistent"])
    assert result.exit_code == 2
    assert "benchmark error" in result.stdout


def test_doctor_exits_nonzero_on_a_broken_benchmark(mini_benchmark):
    result = runner.invoke(app, ["doctor", "-b", str(mini_benchmark(vacuous=True))])
    assert result.exit_code == 1
    assert "BROKEN" in result.stdout
    assert "VACUOUS" in result.stdout


def test_doctor_exits_zero_on_a_healthy_benchmark(mini_benchmark):
    result = runner.invoke(app, ["doctor", "-b", str(mini_benchmark())])
    assert result.exit_code == 0
    assert "All 1 tasks valid" in result.stdout


def test_doctor_reports_excludes_that_hide_fixture_files(mini_benchmark):
    import yaml

    root = mini_benchmark()
    config = yaml.safe_load((root / "benchmark.yaml").read_text())
    config["workspace_excludes"] = ["src/"]
    (root / "benchmark.yaml").write_text(yaml.safe_dump(config))
    result = runner.invoke(app, ["doctor", "-b", str(root)])
    assert result.exit_code == 2
    assert "benchmark error" in result.stdout
    assert "src/thing.py" in result.stdout

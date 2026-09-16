"""Report generation and the end-to-end CLI."""

import json

from typer.testing import CliRunner

from agent_eval.cli import app

from .conftest import REPO_ROOT

runner = CliRunner()


def _demo(tmp_path, real_benchmark_dir, **overrides):
    """A real, offline evaluation over the shipped benchmark, into a temp directory."""
    import yaml
    config = {
        "evaluation": {
            "name": "test evaluation", "benchmark": str(real_benchmark_dir),
            "baseline": str(REPO_ROOT / "harnesses/baseline"),
            "candidate": str(REPO_ROOT / "harnesses/candidate"),
            "runs": 2, "seed": 4, "output_dir": str(tmp_path / "runs"),
            "tasks": ["t02-fix-lookup-bug", "t05-refactor-filtering"],
        },
        "agent": {"adapter": "simulated", "model": "test-model"},
        "judge": {"enabled": True, "provider": "heuristic", "model": "heuristic-demo-1",
                  "self_consistency": 2},
        "decision": {"bootstrap_samples": 500},
    }
    for section, values in overrides.items():
        config[section].update(values)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def test_evaluate_produces_a_complete_report(tmp_path, real_benchmark_dir):
    config = _demo(tmp_path, real_benchmark_dir)
    result = runner.invoke(app, ["evaluate", "--config", str(config)])
    assert result.exit_code == 0, result.output

    evaluations = list((tmp_path / "runs").glob("eval-*"))
    assert len(evaluations) == 1
    directory = evaluations[0]
    for name in ("metadata.json", "plan.jsonl", "summary.json", "report.json", "report.html"):
        assert (directory / name).exists(), f"missing {name}"

    report = json.loads((directory / "report.json").read_text())
    assert report["evaluation"]["n_runs"] == 8      # 2 tasks x 2 reps x 2 arms
    assert report["evaluation"]["n_tasks"] == 2
    assert report["verdict"]["verdict"] in {"POSITIVE", "NEGATIVE", "INCONCLUSIVE"}
    assert report["verdict"]["reasons"], "a verdict with no reasons is not defensible"
    assert report["limitations"]


def test_every_run_leaves_inspectable_evidence(tmp_path, real_benchmark_dir):
    runner.invoke(app, ["evaluate", "--config", str(_demo(tmp_path, real_benchmark_dir))])
    directory = next((tmp_path / "runs").glob("eval-*"))
    for run_dir in directory.glob("runs/*/*/rep-*"):
        for artifact in ("run.json", "stdout.log", "diff.patch", "checks.json", "judge.json"):
            assert (run_dir / artifact).exists(), f"{run_dir}: missing {artifact}"


def test_the_html_report_is_self_contained_and_labelled(tmp_path, real_benchmark_dir):
    runner.invoke(app, ["evaluate", "--config", str(_demo(tmp_path, real_benchmark_dir))])
    html = next((tmp_path / "runs").glob("eval-*/report.html")).read_text()
    assert html.startswith("<!doctype html>")
    # No external resources: the report must open from a file, offline, forever.
    assert "http://" not in html and "https://" not in html
    assert "SIMULATED AGENT" in html, "simulated numbers must be labelled in the report"
    assert "measured" in html and "judged" in html
    assert "Limitations" in html


def test_the_report_states_its_own_resolving_power(tmp_path, real_benchmark_dir):
    runner.invoke(app, ["evaluate", "--config", str(_demo(tmp_path, real_benchmark_dir))])
    report = json.loads(next((tmp_path / "runs").glob("eval-*/report.json")).read_text())
    assert report["power"]["resolvable_effect"] is not None
    assert any("Resolving power" in item for item in report["limitations"])
    assert any("effective n" in item for item in report["limitations"])


def test_report_rebuilds_from_artifacts_without_rerunning_anything(tmp_path, real_benchmark_dir):
    """Changing a threshold must not cost another thirty agent runs."""
    import yaml
    config_path = _demo(tmp_path, real_benchmark_dir)
    runner.invoke(app, ["evaluate", "--config", str(config_path)])
    before = json.loads(next((tmp_path / "runs").glob("eval-*/report.json")).read_text())

    config = yaml.safe_load(config_path.read_text())
    config["decision"]["cost_tolerance"] = 0.0001
    config_path.write_text(yaml.safe_dump(config))

    result = runner.invoke(app, ["report", "--config", str(config_path)])
    assert result.exit_code == 0
    after = json.loads(next((tmp_path / "runs").glob("eval-*/report.json")).read_text())

    assert after["evaluation"]["n_runs"] == before["evaluation"]["n_runs"]
    assert after["runs"] == before["runs"], "rebuilding a report must not alter the evidence"
    assert after["verdict"]["thresholds"]["cost_tolerance"] == 0.0001


def test_judge_can_be_rerun_over_stored_patches(tmp_path, real_benchmark_dir):
    config_path = _demo(tmp_path, real_benchmark_dir)
    runner.invoke(app, ["evaluate", "--config", str(config_path)])
    result = runner.invoke(app, ["judge", "--config", str(config_path), "--provider", "mock"])
    assert result.exit_code == 0
    judged = json.loads(
        next((tmp_path / "runs").glob("eval-*/runs/*/*/rep-*/judge.json")).read_text()
    )
    assert judged["provider"] == "mock"


def test_show_surfaces_one_run(tmp_path, real_benchmark_dir):
    config_path = _demo(tmp_path, real_benchmark_dir)
    runner.invoke(app, ["evaluate", "--config", str(config_path)])
    result = runner.invoke(app, [
        "show", "t02-fix-lookup-bug-candidate-rep01", "--config", str(config_path)
    ])
    assert result.exit_code == 0
    assert "tests" in result.output and "evidence" in result.output


def test_identical_harnesses_are_warned_about(tmp_path, real_benchmark_dir):
    config_path = _demo(
        tmp_path, real_benchmark_dir,
            evaluation={"candidate": str(REPO_ROOT / "harnesses/baseline")}
    )
    result = runner.invoke(app, ["evaluate", "--config", str(config_path)])
    assert "byte-identical" in result.output


def test_evaluate_refuses_a_broken_benchmark(tmp_path, mini_benchmark):
    import yaml
    broken = mini_benchmark(vacuous=True)
    config_path = tmp_path / "c.yaml"
    config_path.write_text(yaml.safe_dump({
        "evaluation": {"benchmark": str(broken),
                       "baseline": str(REPO_ROOT / "harnesses/baseline"),
                       "candidate": str(REPO_ROOT / "harnesses/candidate"), "runs": 1,
                       "output_dir": str(tmp_path / "runs")},
        "agent": {"adapter": "simulated"},
    }))
    result = runner.invoke(app, ["evaluate", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "VACUOUS" in result.output


def test_evaluate_needs_either_a_config_or_all_three_paths():
    result = runner.invoke(app, ["evaluate", "--baseline", str(REPO_ROOT / "harnesses/baseline")])
    assert result.exit_code == 2
    assert "--config" in result.output


def test_a_model_change_is_visible_in_the_report(tmp_path, real_benchmark_dir):
    """Regression test. Pydantic does not serialise properties, so `model_changed` was
    absent from report.json - and a missing key is falsy in Jinja, so the HTML claimed
    'MODEL: unchanged' for a comparison where the model had in fact changed. That is
    precisely the confound the comparability section exists to surface."""
    import shutil

    import yaml

    swapped = tmp_path / "candidate-other-model"
    shutil.copytree(REPO_ROOT / "harnesses" / "candidate", swapped)
    config = yaml.safe_load((swapped / "harness.yaml").read_text())
    config["model"] = "claude-opus-4-1"
    (swapped / "harness.yaml").write_text(yaml.safe_dump(config))

    config_path = _demo(tmp_path, real_benchmark_dir, evaluation={"candidate": str(swapped)})
    runner.invoke(app, ["evaluate", "--config", str(config_path)])

    directory = next((tmp_path / "runs").glob("eval-*"))
    report = json.loads((directory / "report.json").read_text())
    assert report["harness_diff"]["model_changed"] is True
    assert report["verdict"]["verdict"] == "NOT_COMPARABLE"

    html = (directory / "report.html").read_text()
    assert "comparison is confounded" in html


def test_an_unchanged_model_reports_as_unchanged(tmp_path, real_benchmark_dir):
    runner.invoke(app, ["evaluate", "--config", str(_demo(tmp_path, real_benchmark_dir))])
    report = json.loads(next((tmp_path / "runs").glob("eval-*/report.json")).read_text())
    assert report["harness_diff"]["model_changed"] is False
    assert report["harness_diff"]["agent_changed"] is False
    assert report["harness_diff"]["identical"] is False

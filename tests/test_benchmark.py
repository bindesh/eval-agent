import pytest
import yaml

from agent_eval.benchmark import BenchmarkError, load_benchmark

from .conftest import write


def test_loads_the_shipped_benchmark(real_benchmark_dir):
    spec = load_benchmark(real_benchmark_dir)
    assert spec.id == "py-customers"
    assert [t.id for t in spec.tasks] == [
        "t01-export-csv", "t02-fix-lookup-bug", "t03-validate-import",
        "t04-paginate-list", "t05-refactor-filtering",
    ]
    assert all(t.has_reference for t in spec.tasks), "every task needs a reference solution"
    assert all(t.rubric.strip() for t in spec.tasks), "every task needs a rubric"
    assert all(t.verify_dir.is_dir() for t in spec.tasks)


def test_tasks_inherit_benchmark_default_checks(real_benchmark_dir):
    spec = load_benchmark(real_benchmark_dir)
    assert [c.id for c in spec.task("t01-export-csv").checks] == ["tests", "lint", "types"]


def test_extra_checks_are_additive(real_benchmark_dir):
    """t05 adds a no-op guard without restating the benchmark-wide lint/type policy."""
    ids = [c.id for c in load_benchmark(real_benchmark_dir).task("t05-refactor-filtering").checks]
    assert ids == ["tests", "lint", "types", "changed_src"]


def test_protected_paths_are_inherited(real_benchmark_dir):
    task = load_benchmark(real_benchmark_dir).task("t02-fix-lookup-bug")
    assert "tests/**" in task.protected_paths
    assert "verify/**" in task.protected_paths


def test_property_key_maps_to_measures(mini_benchmark):
    spec = load_benchmark(mini_benchmark())
    assert spec.tasks[0].measures == "demo"


def test_benchmarks_without_workspace_excludes_get_an_empty_list(mini_benchmark):
    """Benchmarks that don't set the key must behave exactly as today."""
    spec = load_benchmark(mini_benchmark())
    assert spec.workspace_excludes == []


def test_workspace_excludes_are_loaded_from_benchmark_yaml(mini_benchmark):
    root = mini_benchmark()
    config_path = root / "benchmark.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["workspace_excludes"] = ["target/", "*.o"]
    config_path.write_text(yaml.safe_dump(config))
    spec = load_benchmark(root)
    assert spec.workspace_excludes == ["target/", "*.o"]


def test_missing_fixture_is_an_error(mini_benchmark, tmp_path):
    root = mini_benchmark()
    (root / "benchmark.yaml").write_text(yaml.safe_dump({"id": "x", "fixture": "nope"}))
    with pytest.raises(BenchmarkError, match="fixture directory not found"):
        load_benchmark(root)


def test_missing_prompt_is_an_error(mini_benchmark):
    root = mini_benchmark()
    write(root / "tasks" / "t1" / "task.yaml", yaml.safe_dump({"id": "t1", "title": "x"}))
    with pytest.raises(BenchmarkError, match="missing 'prompt'"):
        load_benchmark(root)


def test_duplicate_check_ids_are_an_error(mini_benchmark):
    root = mini_benchmark()
    write(root / "tasks" / "t1" / "task.yaml", yaml.safe_dump({
        "id": "t1", "title": "x", "prompt": "y",
        "extra_checks": [{"id": "tests", "type": "pytest", "args": []}],
    }))
    with pytest.raises(BenchmarkError, match="duplicate check id"):
        load_benchmark(root)


def test_missing_rubric_file_is_an_error(mini_benchmark):
    root = mini_benchmark()
    write(root / "tasks" / "t1" / "task.yaml", yaml.safe_dump({
        "id": "t1", "title": "x", "prompt": "y", "rubric": "nope.md",
    }))
    with pytest.raises(BenchmarkError, match="rubric file not found"):
        load_benchmark(root)

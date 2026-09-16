"""Loading benchmarks and tasks from disk."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import BenchmarkSpec, CheckSpec, TaskSpec


class BenchmarkError(Exception):
    """Raised when a benchmark or task on disk is malformed."""


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise BenchmarkError(f"missing file: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise BenchmarkError(f"{path} must contain a YAML mapping")
    return data


def load_task(task_dir: Path, *, defaults: dict) -> TaskSpec:
    """Load one task directory, inheriting benchmark-level defaults."""
    data = _read_yaml(task_dir / "task.yaml")

    for required in ("id", "title", "prompt"):
        if not data.get(required):
            raise BenchmarkError(f"{task_dir/'task.yaml'}: missing '{required}'")

    # A task either replaces the benchmark's checks outright, or adds to them.
    # Additive is the common case, so that a project-wide lint/type policy stays in
    # one place and a task only declares what is special about itself.
    if "checks" in data:
        raw_checks = data["checks"]
    else:
        raw_checks = list(defaults.get("default_checks", []))
    raw_checks = list(raw_checks) + list(data.get("extra_checks", []))
    if not raw_checks:
        raise BenchmarkError(f"{task_dir}: task has no checks")
    checks = [CheckSpec(**c) for c in raw_checks]

    seen: set[str] = set()
    for check in checks:
        if check.id in seen:
            raise BenchmarkError(f"{task_dir}: duplicate check id {check.id!r}")
        seen.add(check.id)

    rubric_text = ""
    if data.get("rubric"):
        rubric_path = task_dir / data["rubric"]
        if not rubric_path.exists():
            raise BenchmarkError(f"{task_dir}: rubric file not found: {data['rubric']}")
        rubric_text = rubric_path.read_text()

    return TaskSpec(
        id=data["id"],
        title=data["title"],
        measures=data.get("property", ""),
        prompt=data["prompt"].strip(),
        rubric=rubric_text,
        checks=checks,
        protected_paths=data.get("protected_paths", defaults.get("default_protected_paths", [])),
        timeout_seconds=data.get("timeout_seconds", defaults.get("default_timeout_seconds", 600)),
        directory=task_dir,
        has_reference=(task_dir / "reference").is_dir(),
    )


def load_benchmark(benchmark_dir: Path) -> BenchmarkSpec:
    """Load a benchmark directory and every task under ``tasks/``."""
    benchmark_dir = Path(benchmark_dir).resolve()
    data = _read_yaml(benchmark_dir / "benchmark.yaml")

    fixture_dir = benchmark_dir / data.get("fixture", "fixture")
    if not fixture_dir.is_dir():
        raise BenchmarkError(f"fixture directory not found: {fixture_dir}")

    tasks_root = benchmark_dir / "tasks"
    if not tasks_root.is_dir():
        raise BenchmarkError(f"tasks directory not found: {tasks_root}")

    task_dirs = sorted(d for d in tasks_root.iterdir() if (d / "task.yaml").exists())
    if not task_dirs:
        raise BenchmarkError(f"no tasks found under {tasks_root}")

    tasks = [load_task(d, defaults=data) for d in task_dirs]
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise BenchmarkError(f"duplicate task ids in {tasks_root}")

    return BenchmarkSpec(
        id=data.get("id", benchmark_dir.name),
        title=data.get("title", benchmark_dir.name),
        description=(data.get("description") or "").strip(),
        language=data.get("language", "python"),
        directory=benchmark_dir,
        fixture_dir=fixture_dir,
        tasks=tasks,
    )

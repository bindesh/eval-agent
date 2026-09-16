"""Shared fixtures.

Most tests run against a *synthetic* two-file benchmark built in a tmp_path rather than
the real `py-customers` benchmark. That keeps the suite fast and, more importantly, lets
a test construct deliberately broken benchmarks (vacuous, unsolvable) that we would never
want to commit to the repository.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def real_benchmark_dir() -> Path:
    """The benchmark that ships with the repo."""
    return REPO_ROOT / "benchmarks" / "py-customers"


@pytest.fixture
def mini_benchmark(tmp_path: Path):
    """Build a minimal but complete benchmark; the factory can break it on purpose."""

    def build(*, vacuous: bool = False, solvable: bool = True, with_reference: bool = True) -> Path:
        root = tmp_path / "mini"
        fixture = root / "fixture"
        write(fixture / "pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["src"]\n')
        # A vacuous fixture already satisfies the verification suite.
        body = "def add(a, b):\n    return a + b\n" if vacuous else "def add(a, b):\n    return 0\n"
        write(fixture / "src" / "thing.py", body)

        task = root / "tasks" / "t1"
        write(task / "task.yaml", yaml.safe_dump({
            "id": "t1", "title": "Make add work", "property": "demo",
            "prompt": "Fix add so it adds.",
        }))
        verify = "from thing import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
        if not solvable:
            verify += "\n\ndef test_impossible():\n    assert add(1, 2) == 99\n"
        write(task / "verify" / "test_t1.py", verify)
        if with_reference:
            write(task / "reference" / "src" / "thing.py", "def add(a, b):\n    return a + b\n")

        write(root / "benchmark.yaml", yaml.safe_dump({
            "id": "mini", "title": "Mini", "fixture": "fixture",
            "default_checks": [
                {"id": "tests", "type": "pytest", "args": ["-q", "verify/"],
                 "dimension": "correctness"}
            ],
            "default_protected_paths": ["verify/**"],
        }))
        return root

    return build

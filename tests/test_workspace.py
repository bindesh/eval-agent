from pathlib import Path

import pytest

from agent_eval.workspace import WorkspaceError, create_workspace

from .conftest import write


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    root = tmp_path / "fx"
    write(root / "src" / "app.py", "VALUE = 1\n")
    write(root / "tests" / "test_app.py",
         "from src.app import VALUE\n\n\ndef test():\n    assert VALUE\n")
    return root


def test_workspace_starts_clean(fixture_dir, tmp_path):
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    assert ws.base_commit
    assert ws.changed_files() == []
    assert not ws.has_changes()


def test_detects_and_diffs_a_change(fixture_dir, tmp_path):
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    (ws.path / "src" / "app.py").write_text("VALUE = 2\n")
    assert ws.changed_files() == ["src/app.py"]
    assert ws.has_changes("src/")
    assert "VALUE = 2" in ws.diff()


def test_tool_caches_never_appear_in_the_diff(fixture_dir, tmp_path):
    """Regression test: pytest writes __pycache__ into src/, which once made a
    'did the agent change anything?' check pass for an agent that changed nothing."""
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    (ws.path / "src" / "__pycache__").mkdir()
    (ws.path / "src" / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\x00")
    (ws.path / ".mypy_cache").mkdir()
    (ws.path / ".mypy_cache" / "x.json").write_text("{}")
    assert ws.changed_files() == []
    assert not ws.has_changes("src/")


def test_restore_reverts_modified_protected_file(fixture_dir, tmp_path):
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    (ws.path / "tests" / "test_app.py").write_text("def test():\n    assert True\n")
    touched = ws.restore(["tests/**"])
    assert touched == ["tests/test_app.py"]
    assert "VALUE" in (ws.path / "tests" / "test_app.py").read_text()


def test_restore_recreates_a_deleted_protected_file(fixture_dir, tmp_path):
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    (ws.path / "tests" / "test_app.py").unlink()
    assert ws.restore(["tests/**"]) == ["tests/test_app.py"]
    assert (ws.path / "tests" / "test_app.py").exists()


def test_restore_keeps_newly_added_tests(fixture_dir, tmp_path):
    """A task may ask the agent to add tests. Protection must undo weakening of the
    existing suite without deleting work the task explicitly requested."""
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    (ws.path / "tests" / "test_new.py").write_text("def test_new():\n    assert True\n")
    assert ws.restore(["tests/**"]) == []
    assert (ws.path / "tests" / "test_new.py").exists()


def test_restore_reports_nothing_when_untouched(fixture_dir, tmp_path):
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    assert ws.restore(["tests/**"]) == []


def test_overlay_into_subdirectory(fixture_dir, tmp_path):
    source = tmp_path / "verify"
    write(source / "test_v.py", "def test():\n    assert True\n")
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    assert ws.overlay(source, into="verify") == ["verify/test_v.py"]
    assert (ws.path / "verify" / "test_v.py").exists()


def test_overlay_excluded_from_git_stays_out_of_the_diff(fixture_dir, tmp_path):
    """This is how harness files avoid polluting the patch the agent is graded on."""
    harness = tmp_path / "harness"
    write(harness / "AGENTS.md", "# rules\n")
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    ws.overlay(harness, exclude_from_git=True)
    assert (ws.path / "AGENTS.md").exists()
    assert ws.changed_files() == []
    assert "AGENTS.md" not in ws.diff()


def test_overlay_replaces_existing_files(fixture_dir, tmp_path):
    reference = tmp_path / "ref"
    write(reference / "src" / "app.py", "VALUE = 99\n")
    ws = create_workspace(fixture_dir, tmp_path / "ws")
    ws.overlay(reference)
    assert (ws.path / "src" / "app.py").read_text() == "VALUE = 99\n"


def test_refuses_to_overwrite_an_existing_destination(fixture_dir, tmp_path):
    create_workspace(fixture_dir, tmp_path / "ws")
    with pytest.raises(WorkspaceError, match="already exists"):
        create_workspace(fixture_dir, tmp_path / "ws")

import pytest

from agent_eval.harness import (
    HarnessError,
    diff_harnesses,
    overlay_files,
    snapshot_harness,
)

from .conftest import write


def _harness(root, *, agents="# rules\n", model="claude-sonnet-4-5", extra=None):
    write(root / "AGENTS.md", agents)
    write(root / "harness.yaml", f"name: h\nagent: claude-code\nmodel: {model}\n")
    for rel, text in (extra or {}).items():
        write(root / rel, text)
    return root


def test_identity_is_content_not_location(tmp_path):
    """Two copies of the same harness in different directories are the same harness."""
    a = snapshot_harness(_harness(tmp_path / "a"))
    b = snapshot_harness(_harness(tmp_path / "b"))
    assert a.harness_id == b.harness_id


def test_identity_changes_with_content(tmp_path):
    a = snapshot_harness(_harness(tmp_path / "a"))
    b = snapshot_harness(_harness(tmp_path / "b", agents="# different rules\n"))
    assert a.harness_id != b.harness_id


def test_identity_changes_when_a_file_is_added(tmp_path):
    a = snapshot_harness(_harness(tmp_path / "a"))
    b = snapshot_harness(_harness(tmp_path / "b", extra={".claude/skills/s/SKILL.md": "x"}))
    assert a.harness_id != b.harness_id


def test_identity_changes_when_the_model_changes(tmp_path):
    """A model swap is a harness change; identity must reflect it."""
    a = snapshot_harness(_harness(tmp_path / "a", model="claude-sonnet-4-5"))
    b = snapshot_harness(_harness(tmp_path / "b", model="claude-opus-4-1"))
    assert a.harness_id != b.harness_id


def test_short_id_is_stable_and_prefixed(tmp_path):
    snap = snapshot_harness(_harness(tmp_path / "a"))
    assert snap.short_id.startswith("hns_")
    assert snap.short_id[4:] == snap.harness_id[:8]


def test_overlay_excludes_our_own_config(tmp_path):
    """harness.yaml describes the harness to agent-eval; the agent must never see it
    sitting in the repository it is working on."""
    snap = snapshot_harness(_harness(tmp_path / "a", extra={"hooks/pre.sh": "#!/bin/sh\n"}))
    files = overlay_files(snap)
    assert "harness.yaml" not in files
    assert set(files) == {"AGENTS.md", "hooks/pre.sh"}


def test_empty_harness_is_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(HarnessError, match="empty"):
        snapshot_harness(tmp_path / "empty")


def test_missing_harness_is_an_error(tmp_path):
    with pytest.raises(HarnessError, match="not found"):
        snapshot_harness(tmp_path / "nope")


def test_diff_reports_added_removed_and_modified(tmp_path):
    base = snapshot_harness(_harness(tmp_path / "a", extra={"hooks/old.sh": "old\n"}))
    cand = snapshot_harness(_harness(
        tmp_path / "b", agents="# changed\n", extra={".claude/skills/s/SKILL.md": "new\n"}
    ))
    diff = diff_harnesses(base, cand)
    assert diff.counts() == {"added": 1, "removed": 1, "modified": 1}
    kinds = {c.path: c.change for c in diff.files}
    assert kinds["hooks/old.sh"] == "removed"
    assert kinds[".claude/skills/s/SKILL.md"] == "added"
    assert kinds["AGENTS.md"] == "modified"


def test_modified_text_files_carry_a_unified_diff(tmp_path):
    base = snapshot_harness(_harness(tmp_path / "a", agents="line one\n"))
    cand = snapshot_harness(_harness(tmp_path / "b", agents="line two\n"))
    change = next(c for c in diff_harnesses(base, cand).files if c.path == "AGENTS.md")
    assert "-line one" in change.diff and "+line two" in change.diff


def test_model_change_is_surfaced_as_config_not_a_file_diff(tmp_path):
    base = snapshot_harness(_harness(tmp_path / "a", model="claude-sonnet-4-5"))
    cand = snapshot_harness(_harness(tmp_path / "b", model="claude-opus-4-1"))
    diff = diff_harnesses(base, cand)
    assert diff.model_changed
    assert not any(c.path == "harness.yaml" for c in diff.files)
    change = next(c for c in diff.config if c.key == "model")
    assert (change.before, change.after) == ("claude-sonnet-4-5", "claude-opus-4-1")


def test_name_and_description_are_not_behavioural_changes(tmp_path):
    """Renaming a harness must not read as a configuration change."""
    write(tmp_path / "a" / "AGENTS.md", "x")
    write(tmp_path / "a" / "harness.yaml", "name: before\ndescription: old\nmodel: m\n")
    write(tmp_path / "b" / "AGENTS.md", "x")
    write(tmp_path / "b" / "harness.yaml", "name: after\ndescription: new\nmodel: m\n")
    diff = diff_harnesses(snapshot_harness(tmp_path / "a"), snapshot_harness(tmp_path / "b"))
    assert diff.config == []


def test_identical_harnesses_compare_identical(tmp_path):
    diff = diff_harnesses(
        snapshot_harness(_harness(tmp_path / "a")), snapshot_harness(_harness(tmp_path / "b"))
    )
    assert diff.identical


def test_the_shipped_harnesses_differ_only_in_instructions():
    """Guards the headline evaluation: baseline and candidate must hold the model,
    the agent and the MCP configuration constant, or the comparison is confounded."""
    base = snapshot_harness("harnesses/baseline")
    cand = snapshot_harness("harnesses/candidate")
    diff = diff_harnesses(base, cand)
    assert not diff.model_changed
    assert not diff.agent_changed
    assert diff.config == []
    assert diff.counts()["added"] == 1 and diff.counts()["modified"] == 1


def test_renaming_a_harness_does_not_change_its_identity(tmp_path):
    """`name` and `description` cannot change how the agent behaves, so they are excluded
    from identity. Otherwise renaming a harness would invalidate every recorded cassette
    and make two behaviourally identical harnesses compare as different."""
    write(tmp_path / "a" / "AGENTS.md", "x")
    write(tmp_path / "a" / "harness.yaml", "name: before\ndescription: old\nmodel: m\n")
    write(tmp_path / "b" / "AGENTS.md", "x")
    write(tmp_path / "b" / "harness.yaml", "name: after\ndescription: new\nmodel: m\n")
    assert snapshot_harness(tmp_path / "a").harness_id == (
        snapshot_harness(tmp_path / "b").harness_id
    )


def test_key_order_in_harness_yaml_does_not_change_identity(tmp_path):
    write(tmp_path / "a" / "AGENTS.md", "x")
    write(tmp_path / "a" / "harness.yaml", "model: m\nagent: claude-code\n")
    write(tmp_path / "b" / "AGENTS.md", "x")
    write(tmp_path / "b" / "harness.yaml", "agent: claude-code\nmodel: m\n")
    assert snapshot_harness(tmp_path / "a").harness_id == (
        snapshot_harness(tmp_path / "b").harness_id
    )

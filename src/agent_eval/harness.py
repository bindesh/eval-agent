"""Harness snapshotting, identity and diffing.

A harness is a directory of files that gets overlaid onto the workspace before the agent
runs (AGENTS.md, .claude/skills/..., settings) plus a `harness.yaml` describing what is
not a file: which agent, which model, which MCP servers.

Identity is a **content hash**, not a git SHA. A git SHA identifies a repository state,
not a harness: two commits with identical harness files must compare as the same harness,
and an uncommitted local edit must produce a different id. Recording the git SHA as well
is useful for traceability, but it is metadata, not identity.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

IGNORED_NAMES = {".DS_Store", "__pycache__", ".git", ".ruff_cache", ".mypy_cache"}
CONFIG_FILENAME = "harness.yaml"
# Keys in harness.yaml that describe the harness to a human and cannot change how the
# agent behaves. They are excluded from identity so that renaming a harness does not
# invalidate its recordings, and so two behaviourally identical harnesses compare equal.
NON_BEHAVIOURAL_CONFIG_KEYS = {"name", "description"}
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".sh", ".py", ".ini", ""}


class HarnessError(Exception):
    """Raised when a harness directory cannot be read."""


class HarnessFile(BaseModel):
    """One file in a harness, identified by content rather than by mtime."""

    path: str
    sha256: str
    size: int
    mode: str


class HarnessSnapshot(BaseModel):
    """An immutable record of a harness directory at a point in time."""

    name: str
    source: Path
    harness_id: str
    short_id: str
    files: list[HarnessFile]
    config: dict = Field(default_factory=dict)
    git_commit: str | None = None
    git_dirty: bool | None = None

    @property
    def agent(self) -> str:
        return str(self.config.get("agent", "claude-code"))

    @property
    def model(self) -> str | None:
        value = self.config.get("model")
        return str(value) if value is not None else None

    def file_map(self) -> dict[str, HarnessFile]:
        return {f.path: f for f in self.files}


class HarnessFileChange(BaseModel):
    """One entry in a harness diff."""

    path: str
    change: str  # added | removed | modified
    diff: str = ""


class ConfigChange(BaseModel):
    """One changed key in harness.yaml — model swaps and MCP changes land here."""

    key: str
    before: str | None
    after: str | None


class HarnessDiff(BaseModel):
    """What changed between two harnesses. This is what makes a report actionable."""

    baseline_id: str
    candidate_id: str
    files: list[HarnessFileChange]
    config: list[ConfigChange]

    @property
    def identical(self) -> bool:
        return not self.files and not self.config

    @property
    def model_changed(self) -> bool:
        return any(c.key == "model" for c in self.config)

    @property
    def agent_changed(self) -> bool:
        return any(c.key == "agent" for c in self.config)

    def counts(self) -> dict[str, int]:
        out = {"added": 0, "removed": 0, "modified": 0}
        for change in self.files:
            out[change.change] += 1
        return out


def _iter_files(root: Path):
    for item in sorted(root.rglob("*")):
        if item.is_dir():
            continue
        if any(part in IGNORED_NAMES for part in item.relative_to(root).parts):
            continue
        yield item


def _git_info(root: Path) -> tuple[str | None, bool | None]:
    """Best-effort git provenance. Absent git is normal, not an error."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10
        )
        if commit.returncode != 0:
            return None, None
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", "."],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
        return commit.stdout.strip(), bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment dependent
        return None, None


def snapshot_harness(path: Path, *, name: str | None = None) -> HarnessSnapshot:
    """Read a harness directory and compute its content identity."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise HarnessError(f"harness directory not found: {root}")

    files: list[HarnessFile] = []
    for item in _iter_files(root):
        data = item.read_bytes()
        files.append(
            HarnessFile(
                path=str(item.relative_to(root)),
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                # Only the executable bit matters; a hook that stops being executable is
                # a real harness change, and a umask difference is not.
                mode="755" if item.stat().st_mode & 0o111 else "644",
            )
        )
    if not files:
        raise HarnessError(f"harness directory is empty: {root}")

    config: dict = {}
    config_path = root / CONFIG_FILENAME
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(loaded, dict):
            raise HarnessError(f"{config_path} must contain a YAML mapping")
        config = loaded

    digest = hashlib.sha256()
    for entry in files:
        if entry.path == CONFIG_FILENAME:
            # Hash the behavioural settings, not the file's bytes: a comment, a key
            # reordering or a rename must not produce a different harness.
            behavioural = {
                k: v for k, v in sorted(config.items())
                if k not in NON_BEHAVIOURAL_CONFIG_KEYS
            }
            payload = json.dumps(behavioural, sort_keys=True, default=str)
            digest.update(f"{entry.path}\0{entry.mode}\0".encode())
            digest.update(hashlib.sha256(payload.encode()).hexdigest().encode())
            digest.update(b"\n")
            continue
        digest.update(f"{entry.path}\0{entry.mode}\0{entry.sha256}\n".encode())
    harness_id = digest.hexdigest()

    commit, dirty = _git_info(root)
    return HarnessSnapshot(
        name=name or config.get("name") or root.name,
        source=root,
        harness_id=harness_id,
        short_id=f"hns_{harness_id[:8]}",
        files=files,
        config=config,
        git_commit=commit,
        git_dirty=dirty,
    )


def overlay_files(snapshot: HarnessSnapshot) -> list[str]:
    """Paths that will be copied into a workspace — everything except our own config.

    `harness.yaml` describes the harness to agent-eval; it is not something the agent
    should find sitting in the repository it is working on.
    """
    return [f.path for f in snapshot.files if f.path != CONFIG_FILENAME]


def _text_of(snapshot: HarnessSnapshot, relpath: str) -> list[str] | None:
    path = snapshot.source / relpath
    if path.suffix not in TEXT_SUFFIXES or path.stat().st_size > 200_000:
        return None
    try:
        return path.read_text().splitlines(keepends=True)
    except UnicodeDecodeError:  # pragma: no cover - binary harness file
        return None


def _flatten(config: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in config.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{full}."))
        else:
            out[full] = str(value)
    return out


def diff_harnesses(baseline: HarnessSnapshot, candidate: HarnessSnapshot) -> HarnessDiff:
    """Compare two harnesses file by file and key by key."""
    before, after = baseline.file_map(), candidate.file_map()
    changes: list[HarnessFileChange] = []

    for path in sorted(set(before) | set(after)):
        if path == CONFIG_FILENAME:
            continue  # reported through `config` instead, where it is readable
        old, new = before.get(path), after.get(path)
        if old and not new:
            changes.append(HarnessFileChange(path=path, change="removed"))
        elif new and not old:
            changes.append(HarnessFileChange(path=path, change="added"))
        elif old and new and old.sha256 != new.sha256:
            old_lines, new_lines = _text_of(baseline, path), _text_of(candidate, path)
            text = ""
            if old_lines is not None and new_lines is not None:
                text = "".join(
                    difflib.unified_diff(old_lines, new_lines, f"a/{path}", f"b/{path}", n=2)
                )
            changes.append(HarnessFileChange(path=path, change="modified", diff=text))

    old_cfg, new_cfg = _flatten(baseline.config), _flatten(candidate.config)
    config_changes = [
        ConfigChange(key=key, before=old_cfg.get(key), after=new_cfg.get(key))
        for key in sorted(set(old_cfg) | set(new_cfg))
        # `name` and `description` describe the harness to a human; they are not
        # configuration of the agent and must not read as a behavioural change.
        if key not in {"name", "description"} and old_cfg.get(key) != new_cfg.get(key)
    ]

    return HarnessDiff(
        baseline_id=baseline.short_id, candidate_id=candidate.short_id,
        files=changes, config=config_changes,
    )

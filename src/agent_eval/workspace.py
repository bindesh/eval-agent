"""Building isolated workspaces for a run.

Every agent run, and every `doctor` check, happens in a throwaway copy of the fixture
under git. The git repository is what later lets us produce a diff, restore protected
files, and detect a no-op patch — none of which require the fixture itself to be a
git repository.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

GIT_IDENTITY = [
    "-c", "user.name=agent-eval",
    "-c", "user.email=agent-eval@localhost",
    "-c", "commit.gpgsign=false",
]

# Tool caches are written into the workspace by the checks themselves (pytest writes
# __pycache__ into src/, mypy writes .mypy_cache). Excluding them at the git level is not
# cosmetic: without it every agent diff contains .pyc files, "changed files" is wrong, and
# a "did the agent change anything?" check passes for an agent that did nothing at all.
GIT_EXCLUDES = [
    "__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
    ".coverage", "*.egg-info/", ".DS_Store",
]

IGNORED = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "node_modules", ".DS_Store",
)


class WorkspaceError(Exception):
    """Raised when a workspace cannot be created or inspected."""


@dataclass
class Workspace:
    """A prepared, git-backed copy of a fixture repository."""

    path: Path
    base_commit: str

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *GIT_IDENTITY, *args],
            cwd=self.path, check=check, capture_output=True, text=True,
        )

    # -- inspection -------------------------------------------------------

    def diff(self, *paths: str) -> str:
        """Return the unified diff of the working tree against the base commit."""
        self.git("add", "-A")
        args = ["diff", "--cached", self.base_commit]
        if paths:
            args += ["--", *paths]
        return self.git(*args).stdout

    def changed_files(self) -> list[str]:
        self.git("add", "-A")
        out = self.git("diff", "--cached", "--name-only", self.base_commit).stdout
        return [line for line in out.splitlines() if line]

    def has_changes(self, *paths: str) -> bool:
        """Whether anything under ``paths`` differs from the base commit."""
        self.git("add", "-A")
        args = ["diff", "--cached", "--quiet", self.base_commit]
        if paths:
            args += ["--", *paths]
        # `git diff --quiet` exits 1 when there ARE differences.
        return self.git(*args, check=False).returncode != 0

    # -- mutation ---------------------------------------------------------

    def restore(self, patterns: list[str]) -> list[str]:
        """Restore ``patterns`` from the base commit.

        Returns the list of files that had been modified. Note this reverts changes to
        files that existed at base and re-creates deleted ones, but leaves new untracked
        files alone — an agent asked to *add* tests can still do so, while an agent that
        weakens an existing test has that change undone and recorded.
        """
        if not patterns:
            return []
        self.git("add", "-A")
        # --diff-filter=MD: only files that existed at base and were Modified or Deleted.
        # Added files are deliberately excluded — a task may ask the agent to write new
        # tests, and deleting that work would be wrong. What we are defending against is
        # the agent weakening or removing the verification that was already there.
        out = self.git(
            "diff", "--cached", "--name-only", "--diff-filter=MD",
            self.base_commit, "--", *patterns,
        ).stdout
        touched = [line for line in out.splitlines() if line]
        if touched:
            # Check out the exact files, not the glob, so untracked additions survive.
            self.git("checkout", self.base_commit, "--", *touched, check=False)
            self.git("add", "-A")
        return touched

    def overlay(
        self, source: Path, *, into: str = ".", exclude_from_git: bool = False
    ) -> list[str]:
        """Copy every file under ``source`` into the workspace, returning relative paths.

        ``into`` places the tree under a subdirectory (used for the hidden ``verify/``
        suite); ``exclude_from_git`` hides the copied files from the diff, which is how
        harness files avoid polluting the patch the agent is graded on.
        """
        source = Path(source)
        if not source.is_dir():
            raise WorkspaceError(f"overlay source is not a directory: {source}")
        copied: list[str] = []
        for item in sorted(source.rglob("*")):
            if item.is_dir() or "__pycache__" in item.parts:
                continue
            rel = Path(into) / item.relative_to(source) if into != "." else item.relative_to(source)
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied.append(str(rel))
        if exclude_from_git and copied:
            exclude = self.path / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a") as handle:
                handle.write("\n# agent-eval overlay\n")
                handle.writelines(f"/{rel}\n" for rel in copied)
        return copied


def _refuse_excludes_that_hide_fixture_files(
    destination: Path, patterns: list[str], run: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """Fail if a benchmark's own exclude pattern matches a file shipped in the fixture.

    Such a pattern does not just hide build output: the file is dropped from the base
    commit and every agent edit to it vanishes from the diff, ``changed_files`` and the
    judge's input. ``workspace_excludes: ["src/"]`` would make every agent look like it
    did nothing. That is the same class of silent corruption this list exists to prevent,
    so it is an error at workspace creation (and therefore in ``doctor``), not a warning.
    """
    patterns_file = destination / ".git" / "agent-eval-benchmark-excludes"
    patterns_file.write_text("".join(f"{p}\n" for p in patterns))
    try:
        out = run(
            "ls-files", "--others", "--ignored", f"--exclude-from={patterns_file}"
        ).stdout
    finally:
        patterns_file.unlink(missing_ok=True)
    hidden = [line for line in out.splitlines() if line]
    if hidden:
        shown = ", ".join(hidden[:5]) + (f" (+{len(hidden) - 5} more)" if len(hidden) > 5 else "")
        raise WorkspaceError(
            f"workspace_excludes {patterns!r} match files shipped in the fixture: {shown}. "
            "They would be missing from the base commit and from every agent diff."
        )


def create_workspace(
    fixture_dir: Path, destination: Path, *, extra_excludes: list[str] | None = None
) -> Workspace:
    """Copy ``fixture_dir`` to ``destination`` and make it a git repo with one commit.

    ``extra_excludes`` is a benchmark's own gitignore-style patterns (a Rust `target/`,
    a Go binary, a Gradle `build/`) — applied on top of ``GIT_EXCLUDES``, never instead
    of them, so a benchmark cannot accidentally re-expose the Python/JS caches the fixed
    list already guards against.
    """
    fixture_dir = Path(fixture_dir).resolve()
    destination = Path(destination)
    if destination.exists():
        raise WorkspaceError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture_dir, destination, ignore=IGNORED)

    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *GIT_IDENTITY, *a], cwd=destination, check=True, capture_output=True, text=True
    )
    run("init", "-q", "-b", "main")
    exclude = destination / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    if extra_excludes:
        try:
            _refuse_excludes_that_hide_fixture_files(destination, extra_excludes, run)
        except WorkspaceError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    with exclude.open("a") as handle:
        handle.write("\n# agent-eval: tool caches, never part of a patch\n")
        handle.writelines(f"{pattern}\n" for pattern in GIT_EXCLUDES)
        if extra_excludes:
            handle.write("\n# agent-eval: benchmark-specific workspace_excludes\n")
            handle.writelines(f"{pattern}\n" for pattern in extra_excludes)
    run("add", "-A")
    run("commit", "-q", "-m", "agent-eval base", "--allow-empty")
    sha = run("rev-parse", "HEAD").stdout.strip()
    return Workspace(path=destination, base_commit=sha)

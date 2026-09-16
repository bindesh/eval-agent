"""Running objective checks against a workspace.

Every check is a subprocess with an exit code. That is deliberate: an exit code is the
least arguable signal available, it needs no parsing to be trustworthy, and it lets a
benchmark add a project-specific check (a custom script, a build step) without the
evaluator learning anything new.
"""

from __future__ import annotations

import subprocess
import sys
import time

from ..models import CheckOutcome, CheckSpec
from ..workspace import Workspace

MAX_CAPTURE = 60_000  # characters kept per stream in the artifact


def _as_text(value: object) -> str:
    """subprocess timeouts can hand back bytes, str or None depending on the platform."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _truncate(text: str) -> str:
    if len(text) <= MAX_CAPTURE:
        return text
    half = MAX_CAPTURE // 2
    omitted = len(text) - 2 * half
    marker = f"\n\n... [{omitted} characters omitted by agent-eval] ...\n\n"
    return f"{text[:half]}{marker}{text[-half:]}"


def _resolve(cmd: list[str], python: str) -> list[str]:
    """Substitute ``{python}`` so a benchmark never hardcodes an interpreter path."""
    return [python if part == "{python}" else part for part in cmd]


def run_check(spec: CheckSpec, workspace: Workspace, *, python: str | None = None) -> CheckOutcome:
    """Run one check in ``workspace`` and return its outcome."""
    python = python or sys.executable

    if spec.type == "non_empty_diff":
        # Not a subprocess: asks git whether the agent changed anything under `paths`.
        started = time.monotonic()
        changed = workspace.has_changes(*spec.paths)
        return CheckOutcome(
            id=spec.id, type=spec.type, dimension=spec.dimension,
            passed=changed, exit_code=0 if changed else 1,
            duration_s=time.monotonic() - started,
            command=["<git diff>", *spec.paths],
            stdout=(
                "workspace differs from base" if changed
                else "no changes under " + ", ".join(spec.paths)
            ),
        )

    if spec.type == "pytest":
        cmd = [python, "-m", "pytest", *spec.args]
    elif spec.type == "command":
        if not spec.cmd:
            raise ValueError(f"check {spec.id!r} of type 'command' has no cmd")
        cmd = _resolve(spec.cmd, python)
    else:  # pragma: no cover - guarded by the Literal type
        raise ValueError(f"unknown check type: {spec.type}")

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=workspace.path, capture_output=True, text=True,
            timeout=spec.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return CheckOutcome(
            id=spec.id, type=spec.type, dimension=spec.dimension, passed=False,
            exit_code=None, timed_out=True, duration_s=time.monotonic() - started,
            command=cmd,
            stdout=_truncate(_as_text(exc.stdout)),
            stderr=_truncate(_as_text(exc.stderr)),
        )

    return CheckOutcome(
        id=spec.id, type=spec.type, dimension=spec.dimension,
        passed=proc.returncode == 0, exit_code=proc.returncode,
        duration_s=time.monotonic() - started, command=cmd,
        stdout=_truncate(proc.stdout), stderr=_truncate(proc.stderr),
    )


def run_checks(
    specs: list[CheckSpec], workspace: Workspace, *, python: str | None = None
) -> list[CheckOutcome]:
    """Run every check, in declaration order. All checks run even if an early one fails."""
    return [run_check(spec, workspace, python=python) for spec in specs]

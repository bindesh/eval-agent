"""Loading configuration from a local `.env` file.

Credentials should not have to live in a shell profile to run this tool, and they should
never live in the repository. A gitignored `.env` next to the config is the usual middle
ground: convenient, local, and one line in `.gitignore` away from being committed.

Deliberate choices:

* **The real environment always wins.** A value already exported is never overwritten, so
  a `.env` left over from last week cannot silently override what you just set in the
  shell, and CI keeps control of its own secrets.
* **No dependency.** The format is a handful of lines of parsing, and a dependency that
  reads secrets is a dependency worth not having.
* **Values are never echoed.** Loading reports only the names it set, never the values —
  and `redact.py` scrubs anything secret-shaped out of artifacts regardless.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_FILENAME = ".env"


def parse_env_file(text: str) -> dict[str, str]:
    """Parse `KEY=value` lines. Supports `export`, comments, and quoted values."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            # An unquoted trailing comment is not part of the value.
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def find_env_file(start: Path | None = None) -> Path | None:
    """Look for a `.env` beside the config, then upwards to the repository root."""
    current = (start or Path.cwd()).resolve()
    candidates = [current, *current.parents]
    for directory in candidates[:6]:
        candidate = directory / DEFAULT_FILENAME
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def load_env(path: Path | None = None, *, start: Path | None = None) -> list[str]:
    """Load a `.env` into the process environment. Returns the NAMES it set."""
    env_path = path or find_env_file(start)
    if env_path is None or not env_path.is_file():
        return []
    applied: list[str] = []
    for key, value in parse_env_file(env_path.read_text()).items():
        if key in os.environ:      # the shell wins, always
            continue
        os.environ[key] = value
        applied.append(key)
    return applied

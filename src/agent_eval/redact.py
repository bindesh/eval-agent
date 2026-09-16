"""Secret redaction.

Everything an agent prints ends up in an artifact file, and artifacts are meant to be
read, shared and attached to a report. Anything that looks like a credential is replaced
before it is written to disk — not when it is displayed, because by then it has already
been persisted.
"""

from __future__ import annotations

import os
import re

PLACEHOLDER = "[REDACTED]"

PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[=:]\s*\"?[^\s\"']{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

SENSITIVE_ENV_NAME = re.compile(r"(?i)(key|token|secret|password|passwd|credential)")

# Environment keys worth recording for reproducibility. An allow-list, because a full
# environment dump is the most reliable way to leak a credential into an artifact.
ENV_ALLOWLIST = ("PATH", "SHELL", "LANG", "TERM", "CI", "VIRTUAL_ENV")


def redact(text: str) -> str:
    """Replace anything credential-shaped in ``text``."""
    if not text:
        return text
    for pattern in PATTERNS:
        text = pattern.sub(PLACEHOLDER, text)
    # Also redact the literal value of any secret-looking env var that appears verbatim.
    for name, value in os.environ.items():
        if value and len(value) >= 8 and SENSITIVE_ENV_NAME.search(name):
            text = text.replace(value, PLACEHOLDER)
    return text


def safe_env_snapshot() -> dict[str, str]:
    """Allow-listed environment metadata, with secret-looking names reported as present."""
    snapshot = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    snapshot["_secret_env_names"] = ",".join(
        sorted(k for k in os.environ if SENSITIVE_ENV_NAME.search(k))
    )
    return snapshot

"""Reusable field validators.

Input validation goes through these helpers so that every failure produces a
:class:`~customers.errors.ValidationError` carrying the offending field name.
"""

from collections.abc import Sequence

from .errors import ValidationError


def require_non_empty(value: object, field: str) -> str:
    """Return ``value`` as a stripped string, or raise for empty/non-string input."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(field, "must be a non-empty string")
    return value.strip()


def require_email(value: object, field: str) -> str:
    """Return ``value`` as a stripped email address, or raise."""
    text = require_non_empty(value, field)
    if "@" not in text:
        raise ValidationError(field, "must be a valid email address")
    return text


def require_choice(value: object, field: str, choices: Sequence[str]) -> str:
    """Return ``value`` if it is one of ``choices``, otherwise raise."""
    if value not in choices:
        raise ValidationError(field, f"must be one of {', '.join(choices)}")
    return str(value)

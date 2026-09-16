"""Domain records."""

from dataclasses import dataclass

VALID_STATUSES = ("active", "inactive")


@dataclass(frozen=True)
class Customer:
    """A customer record."""

    id: str
    name: str
    email: str
    status: str = "active"

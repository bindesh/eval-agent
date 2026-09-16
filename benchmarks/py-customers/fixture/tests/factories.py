"""Test data factories. Tests build records through these, never inline."""

from customers import Customer


def make_customer(
    id: str = "c1",
    name: str = "Ada Lovelace",
    email: str = "ada@example.com",
    status: str = "active",
) -> Customer:
    """Return a Customer with sensible defaults."""
    return Customer(id=id, name=name, email=email, status=status)

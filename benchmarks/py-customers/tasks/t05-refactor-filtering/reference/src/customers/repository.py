"""Storage for customer records."""

from .errors import NotFoundError
from .models import Customer


def customer_matches(
    customer: Customer, *, status: str | None = None, search: str | None = None
) -> bool:
    """Return whether ``customer`` satisfies the given status and search filters.

    Lives at module level so every repository implementation can share one
    definition of what the filters mean.
    """
    if status is not None and customer.status != status:
        return False
    if search is not None:
        needle = search.lower()
        if needle not in customer.name.lower() and needle not in customer.email.lower():
            return False
    return True


class InMemoryCustomerRepository:
    """Keeps customers in a dict, ordered by id when listed."""

    def __init__(self) -> None:
        self._items: dict[str, Customer] = {}

    def add(self, customer: Customer) -> Customer:
        """Insert or replace ``customer`` and return it."""
        self._items[customer.id] = customer
        return customer

    def get(self, customer_id: str) -> Customer:
        """Return the customer with ``customer_id`` or raise ``NotFoundError``."""
        try:
            return self._items[customer_id]
        except KeyError:
            raise NotFoundError("Customer", customer_id) from None

    def find(self, customer_id: str) -> Customer | None:
        """Return the customer with ``customer_id``, or ``None`` if absent."""
        return self._items.get(customer_id)

    def list_all(
        self, *, status: str | None = None, search: str | None = None
    ) -> list[Customer]:
        """Return customers sorted by id, optionally filtered by status and search term."""
        return [
            customer
            for customer in sorted(self._items.values(), key=lambda c: c.id)
            if customer_matches(customer, status=status, search=search)
        ]

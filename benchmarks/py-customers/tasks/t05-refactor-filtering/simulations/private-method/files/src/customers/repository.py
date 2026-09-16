"""Storage for customer records."""

from .errors import NotFoundError
from .models import Customer


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
            if self._matches(customer, status, search)
        ]

    def _matches(self, customer: Customer, status: str | None, search: str | None) -> bool:
        """Return whether ``customer`` satisfies the filters."""
        if status is not None and customer.status != status:
            return False
        if search is not None:
            needle = search.lower()
            if needle not in customer.name.lower() and needle not in customer.email.lower():
                return False
        return True

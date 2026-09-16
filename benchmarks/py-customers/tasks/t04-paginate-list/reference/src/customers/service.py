"""The public API used by callers of this package."""

from collections.abc import Iterable, Mapping

from .models import VALID_STATUSES, Customer
from .repository import InMemoryCustomerRepository
from .validation import require_choice, require_email, require_non_empty


class CustomerService:
    """Coordinates validation and storage for customer records."""

    def __init__(self, repository: InMemoryCustomerRepository | None = None) -> None:
        self._repository = repository or InMemoryCustomerRepository()

    def create_customer(
        self, *, id: str, name: str, email: str, status: str = "active"
    ) -> Customer:
        """Validate and store a new customer."""
        customer = Customer(
            id=require_non_empty(id, "id"),
            name=require_non_empty(name, "name"),
            email=require_email(email, "email"),
            status=require_choice(status, "status", VALID_STATUSES),
        )
        return self._repository.add(customer)

    def get_customer(self, customer_id: str) -> Customer | None:
        """Return the customer with ``customer_id``."""
        return self._repository.find(customer_id)

    def list_customers(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Customer]:
        """Return customers sorted by id, optionally filtered and paged."""
        return self._repository.list_all(
            status=status, search=search, limit=limit, offset=offset
        )

    def import_customers(self, rows: Iterable[Mapping[str, str]]) -> list[Customer]:
        """Store a batch of customer rows coming from an uploaded file."""
        imported: list[Customer] = []
        for row in rows:
            customer = Customer(
                id=row["id"],
                name=row["name"],
                email=row["email"],
                status=row.get("status", "active"),
            )
            imported.append(self._repository.add(customer))
        return imported

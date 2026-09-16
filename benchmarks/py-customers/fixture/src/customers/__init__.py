"""Customer records service."""

from .errors import AppError, NotFoundError, ValidationError
from .models import VALID_STATUSES, Customer
from .repository import InMemoryCustomerRepository
from .service import CustomerService

__all__ = [
    "VALID_STATUSES",
    "AppError",
    "Customer",
    "CustomerService",
    "InMemoryCustomerRepository",
    "NotFoundError",
    "ValidationError",
]

"""Export helpers for customer records."""

import csv
import io
from collections.abc import Iterable

from .models import Customer

CSV_FIELDS = ("id", "name", "email", "status")


def customers_to_csv(customers: Iterable[Customer]) -> str:
    """Render ``customers`` as a CSV string with a header row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_FIELDS)
    for customer in customers:
        writer.writerow([customer.id, customer.name, customer.email, customer.status])
    return buffer.getvalue()

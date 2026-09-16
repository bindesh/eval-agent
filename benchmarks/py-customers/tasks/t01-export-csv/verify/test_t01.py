import csv
import io

import pytest

from customers import CustomerService


def _service():
    service = CustomerService()
    service.create_customer(id="c2", name="Bob Stone", email="bob@example.com")
    service.create_customer(id="c1", name="Ada Lovelace", email="ada@example.com")
    service.create_customer(id="c3", name="Cy Ltd, Inc", email="cy@example.com", status="inactive")
    return service


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def test_export_csv_exists():
    assert hasattr(CustomerService, "export_csv"), "CustomerService.export_csv is missing"


def test_export_csv_header_and_order():
    rows = _rows(_service().export_csv())
    assert rows[0] == ["id", "name", "email", "status"]
    assert [r[0] for r in rows[1:]] == ["c1", "c2", "c3"]


def test_export_csv_row_contents():
    rows = _rows(_service().export_csv())
    assert rows[1] == ["c1", "Ada Lovelace", "ada@example.com", "active"]


def test_export_csv_escapes_commas():
    rows = _rows(_service().export_csv())
    assert rows[3] == ["c3", "Cy Ltd, Inc", "cy@example.com", "inactive"]


def test_export_csv_honours_status_filter():
    rows = _rows(_service().export_csv(status="inactive"))
    assert [r[0] for r in rows[1:]] == ["c3"]


def test_export_csv_honours_search_filter():
    rows = _rows(_service().export_csv(search="ada"))
    assert [r[0] for r in rows[1:]] == ["c1"]


def test_export_csv_empty_repository_has_header_only():
    rows = [r for r in _rows(CustomerService().export_csv()) if r]
    assert rows == [["id", "name", "email", "status"]]


@pytest.mark.parametrize("status", ["active", "inactive"])
def test_export_csv_status_values_roundtrip(status):
    service = CustomerService()
    service.create_customer(id="x1", name="X", email="x@example.com", status=status)
    assert _rows(service.export_csv())[1][3] == status

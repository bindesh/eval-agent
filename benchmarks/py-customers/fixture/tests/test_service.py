import pytest

from customers import CustomerService, ValidationError
from tests.factories import make_customer


def test_create_customer_stores_record():
    service = CustomerService()
    created = service.create_customer(id="c1", name="Ada", email="ada@example.com")
    assert created.id == "c1"
    assert service.get_customer("c1") == created


def test_create_customer_rejects_bad_email():
    service = CustomerService()
    with pytest.raises(ValidationError) as excinfo:
        service.create_customer(id="c1", name="Ada", email="not-an-email")
    assert excinfo.value.field == "email"


def test_list_customers_is_sorted_by_id():
    service = CustomerService()
    service.create_customer(id="c2", name="Bob", email="bob@example.com")
    service.create_customer(id="c1", name="Ada", email="ada@example.com")
    assert [c.id for c in service.list_customers()] == ["c1", "c2"]


def test_list_customers_filters_by_status():
    service = CustomerService()
    service.create_customer(id="c1", name="Ada", email="ada@example.com", status="active")
    service.create_customer(id="c2", name="Bob", email="bob@example.com", status="inactive")
    assert [c.id for c in service.list_customers(status="inactive")] == ["c2"]


def test_import_customers_returns_records():
    service = CustomerService()
    rows = [{"id": "c1", "name": "Ada", "email": "ada@example.com"}]
    assert [c.id for c in service.import_customers(rows)] == ["c1"]


def test_factory_defaults():
    assert make_customer().status == "active"

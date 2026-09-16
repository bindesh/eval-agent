import pytest

from customers import CustomerService, ValidationError


def _rows(**overrides):
    row = {"id": "c1", "name": "Ada", "email": "ada@example.com", "status": "active"}
    row.update(overrides)
    return [row]


def test_valid_rows_still_import():
    assert [c.id for c in CustomerService().import_customers(_rows())] == ["c1"]


def test_status_defaults_to_active_when_absent():
    row = {"id": "c1", "name": "Ada", "email": "ada@example.com"}
    assert CustomerService().import_customers([row])[0].status == "active"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"id": ""}, "id"),
        ({"email": "nope"}, "email"),
        ({"status": "archived"}, "status"),
    ],
)
def test_invalid_row_raises_validation_error_naming_the_field(overrides, field):
    with pytest.raises(ValidationError) as excinfo:
        CustomerService().import_customers(_rows(**overrides))
    assert excinfo.value.field == field


def test_missing_column_is_a_validation_error_not_a_key_error():
    row = {"id": "c1", "email": "ada@example.com"}
    with pytest.raises(ValidationError):
        CustomerService().import_customers([row])


def test_invalid_row_is_not_stored():
    service = CustomerService()
    with pytest.raises(ValidationError):
        service.import_customers(_rows(email="nope"))
    assert service.list_customers() == []

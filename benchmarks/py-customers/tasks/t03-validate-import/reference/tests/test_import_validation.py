import pytest

from customers import CustomerService, ValidationError


def _row(**overrides):
    row = {"id": "c1", "name": "Ada", "email": "ada@example.com", "status": "active"}
    row.update(overrides)
    return row


def test_import_accepts_a_valid_row():
    assert [c.id for c in CustomerService().import_customers([_row()])] == ["c1"]


def test_import_defaults_missing_status_to_active():
    row = {"id": "c1", "name": "Ada", "email": "ada@example.com"}
    assert CustomerService().import_customers([row])[0].status == "active"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [({"name": ""}, "name"), ({"email": "nope"}, "email"), ({"status": "archived"}, "status")],
)
def test_import_rejects_invalid_fields(overrides, field):
    with pytest.raises(ValidationError) as excinfo:
        CustomerService().import_customers([_row(**overrides)])
    assert excinfo.value.field == field


def test_import_reports_a_missing_column_as_validation_error():
    with pytest.raises(ValidationError):
        CustomerService().import_customers([{"id": "c1", "email": "ada@example.com"}])

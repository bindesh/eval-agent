import pytest

from customers import CustomerService


def test_import_rejects_a_blank_name():
    with pytest.raises(ValueError):
        CustomerService().import_customers([{"id": "c1", "name": "", "email": "a@b.c"}])

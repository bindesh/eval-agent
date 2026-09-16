import inspect

import pytest

from customers import AppError, CustomerService, NotFoundError


def _service():
    service = CustomerService()
    service.create_customer(id="c1", name="Ada Lovelace", email="ada@example.com")
    return service


def test_existing_lookup_still_works():
    assert _service().get_customer("c1").name == "Ada Lovelace"


def test_missing_lookup_raises_not_found():
    with pytest.raises(NotFoundError):
        _service().get_customer("nope")


def test_raised_error_is_an_app_error():
    with pytest.raises(AppError) as excinfo:
        _service().get_customer("nope")
    assert excinfo.value.code == "not_found"


def test_error_identifies_entity_and_id():
    with pytest.raises(NotFoundError) as excinfo:
        _service().get_customer("nope")
    assert excinfo.value.entity == "Customer"
    assert excinfo.value.identifier == "nope"


def test_does_not_return_none_for_missing():
    service = _service()
    try:
        result = service.get_customer("nope")
    except AppError:
        return
    pytest.fail(f"get_customer returned {result!r} instead of raising")


def test_return_annotation_is_not_optional():
    annotation = str(inspect.signature(CustomerService.get_customer).return_annotation)
    assert "None" not in annotation, f"return annotation still optional: {annotation}"

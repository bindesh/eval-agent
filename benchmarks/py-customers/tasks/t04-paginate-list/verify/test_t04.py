import pytest

from customers import CustomerService


def _service(count=5):
    service = CustomerService()
    for i in range(1, count + 1):
        service.create_customer(
            id=f"c{i}", name=f"Name {i}", email=f"n{i}@example.com",
            status="active" if i % 2 else "inactive",
        )
    return service


# --- backwards compatibility: these passed before the change and must still pass ---

def test_no_argument_call_returns_a_list():
    assert isinstance(_service().list_customers(), list)


def test_no_argument_call_returns_everything_in_order():
    assert [c.id for c in _service().list_customers()] == ["c1", "c2", "c3", "c4", "c5"]


def test_existing_status_filter_unchanged():
    assert [c.id for c in _service().list_customers(status="inactive")] == ["c2", "c4"]


def test_existing_search_filter_unchanged():
    assert [c.id for c in _service().list_customers(search="n3@")] == ["c3"]


# --- the new behaviour ---

def test_limit_returns_first_n():
    assert [c.id for c in _service().list_customers(limit=2)] == ["c1", "c2"]


def test_offset_skips():
    assert [c.id for c in _service().list_customers(offset=3)] == ["c4", "c5"]


def test_limit_and_offset_together():
    assert [c.id for c in _service().list_customers(limit=2, offset=1)] == ["c2", "c3"]


def test_paging_composes_with_status_filter():
    assert [c.id for c in _service().list_customers(status="active", limit=2)] == ["c1", "c3"]


def test_offset_past_the_end_is_empty():
    assert _service().list_customers(offset=99) == []


def test_pagination_parameters_are_keyword_only():
    with pytest.raises(TypeError):
        _service().list_customers(2)

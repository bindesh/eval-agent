"""Behaviour-preservation suite for the filtering refactor.

Every assertion here describes behaviour that already worked before the refactor.
The patch is correct if none of it changed.
"""

from customers import Customer, InMemoryCustomerRepository


def _repo():
    repo = InMemoryCustomerRepository()
    repo.add(Customer(id="c2", name="Bob Stone", email="bob@example.com", status="inactive"))
    repo.add(Customer(id="c1", name="Ada Lovelace", email="ada@example.com", status="active"))
    repo.add(Customer(id="c3", name="Cy Young", email="cy@work.example", status="active"))
    return repo


def test_list_all_sorted_by_id():
    assert [c.id for c in _repo().list_all()] == ["c1", "c2", "c3"]


def test_list_all_filters_by_status():
    assert [c.id for c in _repo().list_all(status="active")] == ["c1", "c3"]


def test_search_matches_name_case_insensitively():
    assert [c.id for c in _repo().list_all(search="ADA")] == ["c1"]


def test_search_matches_email():
    assert [c.id for c in _repo().list_all(search="work.example")] == ["c3"]


def test_search_and_status_combine():
    assert [c.id for c in _repo().list_all(status="active", search="cy")] == ["c3"]


def test_search_with_no_match_is_empty():
    assert _repo().list_all(search="zzz") == []


def test_filters_are_keyword_only():
    repo = _repo()
    try:
        repo.list_all("active")
    except TypeError:
        return
    raise AssertionError("list_all should keep its keyword-only signature")


def test_get_and_find_unchanged():
    repo = _repo()
    assert repo.get("c1").name == "Ada Lovelace"
    assert repo.find("nope") is None

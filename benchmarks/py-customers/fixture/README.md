# customers

A small customer-records service used by the billing and support teams.

## Layout

    src/customers/errors.py       application error types
    src/customers/models.py       the Customer record
    src/customers/validation.py   reusable field validators
    src/customers/repository.py   storage
    src/customers/service.py      the public API used by callers
    tests/                        test suite

## Development

    pytest
    ruff check .
    mypy src/

# Rubric — t03 Validate imported customer rows

## requirement_completeness
Are id, name, email and status all validated; is a missing status defaulted to
`active`; is a missing column reported as a validation failure rather than a raw
`KeyError`; and can the caller tell which field failed?
- 5: every field validated, field name available on the error, missing column handled.
- 3: some fields validated, or the failing field is not identifiable.
- 1: no effective validation.

## convention_adherence
Does the patch validate through the existing helpers in `validation.py` rather than
writing inline `if ...: raise` checks, use `VALID_STATUSES` rather than a new literal
list, and add tests under `tests/` that follow the existing style (pytest functions,
records built through `tests/factories.py` where applicable)?

## maintainability
Were tests actually added and are they meaningful (covering the failure cases, not
only the happy path)? Is the validation logic free of duplication?

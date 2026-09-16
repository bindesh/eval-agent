# Rubric — t01 Add CSV export

## requirement_completeness
Does the patch add a CSV export on `CustomerService` named `export_csv`, producing a
header row `id,name,email,status` and one row per customer, honouring the same
`status`/`search` filters as `list_customers()`?
- 5: all of the above, including correct field quoting/escaping.
- 3: exports data but misses filters, ordering, or escaping.
- 1: no working export.

## convention_adherence
Does the patch match how this repository is written? Public methods carry type hints
and docstrings; new behaviour is placed in a sensible module; errors (if any) derive
from `AppError`; nothing in `src/` is restructured unnecessarily.

## maintainability
Is the CSV produced with the standard library `csv` module rather than manual string
concatenation? Is the code readable, and is duplication avoided?

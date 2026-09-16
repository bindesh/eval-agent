# Rubric — t04 Paginate the customer list

## requirement_completeness
Can a caller request a page (a maximum number of results, and a number to skip), does
paging compose with the `status` and `search` filters, and does ordering stay stable
across pages?
- 5: limit and offset both work, compose with filters, ordering stable.
- 3: one of limit/offset works, or paging ignores the filters.
- 1: no working pagination.

## convention_adherence
The last line of the task ("several services already call list_customers() and we are
not changing them") is a backwards-compatibility requirement. Does an existing
no-argument call still behave exactly as before — same return type, same contents?
A patch that changes the return type to a page/envelope object, makes limit
mandatory, or applies a default limit scores 1 here regardless of how good the
pagination is. Are the new parameters keyword-only, matching the existing signature?

## maintainability
Is the paging applied in one place rather than duplicated across the repository and
the service? Are the new parameters typed and documented?

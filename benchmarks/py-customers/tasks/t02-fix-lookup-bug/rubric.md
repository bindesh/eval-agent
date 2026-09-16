# Rubric — t02 Fix the silent miss in customer lookup

## requirement_completeness
Does looking up an unknown id now fail loudly instead of returning ``None``, while
lookups of existing ids still return the customer unchanged?
- 5: both, and the failure carries which entity and which id was missing.
- 3: fails loudly but loses the identifying detail, or changes existing behaviour.
- 1: still returns ``None``, or breaks existing lookups.

## convention_adherence
This repository maps errors onto HTTP responses through `AppError.code`. Does the
patch raise an error type from `errors.py` (rather than a builtin such as
`ValueError`/`KeyError`), and does it reuse the repository method that already
implements this behaviour rather than duplicating it? Is the return type annotation
updated to match the new contract?

## maintainability
Is the fix minimal and in the right layer? A patch that rewrites unrelated code, or
that adds a second lookup path alongside the existing one, scores lower.

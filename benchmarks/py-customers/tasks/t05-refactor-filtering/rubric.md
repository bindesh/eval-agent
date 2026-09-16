# Rubric — t05 Separate filtering from storage

## requirement_completeness
Is the matching logic now reusable from outside `InMemoryCustomerRepository` — a
module-level function, a small class, or an equivalent seam that a second repository
could import — rather than still being inline in `list_all`?
- 5: a clear reusable seam; `list_all` now reads as storage plus a call to it.
- 3: some extraction, but still coupled to the in-memory repository (e.g. a private
  method on the class that a different repository could not use).
- 1: no meaningful extraction, or the logic was merely moved without being reusable.

## convention_adherence
Type hints and docstrings on anything new, matching the surrounding style; the public
surface of `repository.py` unchanged; no new dependencies; imports ordered as ruff
requires.

## maintainability
Is the result genuinely simpler to extend than what was there before? Note explicitly
whether the patch changed observable behaviour anywhere — a refactor that alters
ordering, filter semantics, or the signature of `list_all` fails its own brief.

---
name: repo-conventions
description: >
  Use before editing any Python module in this repository, to find and follow the
  conventions already in the codebase instead of inventing new ones.
---

# Finding this repository's conventions

Do this before writing code, not after.

## 1. Find the error convention

    grep -rn "class .*Error" src/

Most codebases define their own error hierarchy and map it onto responses. If one
exists, raise from it. Raising a builtin (`ValueError`, `KeyError`) where the project
has its own error type is a defect, even when the behaviour looks right.

## 2. Find the validation convention

    ls src/*/validation.py src/*/validators.py 2>/dev/null
    grep -rn "def require_\|def validate_" src/

If shared validators exist, route new validation through them rather than writing
inline `if not x: raise` checks. Shared validators usually carry structured detail
(such as which field failed) that inline checks silently drop.

## 3. Find the test convention

    ls tests/
    grep -rn "def make_\|factory" tests/

Put new tests where the existing ones live, name them the same way, and build test
data through existing factories.

## 4. Check who calls what you are about to change

    grep -rn "name_of_function" src/ tests/

If a function has existing callers and the task did not ask you to change them, your
change must keep those call sites working. Adding optional keyword arguments is safe;
changing a return type or making an argument required is not.

## 5. Verify before finishing

    pytest && ruff check src/ tests/ && mypy src/

# AGENTS.md

This is a Python project.

- Run the tests with `pytest`.
- Keep the code tidy.

## Before you write code

This codebase has established patterns. Before adding anything, read the modules
next to the one you are changing and follow what they already do. In particular,
look for how the codebase already handles the thing you are about to handle - error
signalling, input validation, test setup - and reuse it rather than inventing a
second way.

## Working rules

- Prefer extending an existing helper over writing a parallel one.
- Match the surrounding style: type hints and a docstring on anything public.
- When you change a function other code already calls, keep the existing call
  working unless you were asked to break it.
- Run `pytest`, `ruff check src/ tests/` and `mypy src/` before you finish, and fix
  what they report.

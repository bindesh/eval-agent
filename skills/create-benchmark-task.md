---
name: create-benchmark-task
description: >
  Use when adding a task to an agent-eval benchmark, to produce one that can actually
  distinguish two harnesses instead of silently diluting every result.
---

# Creating a benchmark task

A task that cannot distinguish two harnesses is worse than no task: it drags every measured
delta toward zero and **is invisible in the final report**. Most of this skill is about
avoiding that.

## 1. Decide what property the task tests

Look at what the benchmark already covers. A benchmark of five "implement a feature" tasks
measures one thing five times. Aim for a spread:

| Property | Shape |
|---|---|
| baseline capability | implement something missing |
| convention adherence | fix a bug where the repo already has a house pattern to follow |
| instruction following | a requirement with two or three distinct parts |
| regression awareness | change an API that existing callers depend on |
| behaviour preservation | a refactor — near-zero functional signal, high judged signal |

## 2. Make the convention discoverable without the harness

This is the trap. If the hidden tests assert something only stated in the candidate
`AGENTS.md`, the candidate wins trivially and the evaluation is theatre.

The rule: **the harness may be a hint, never a secret key.** Whatever the tests assert must
be inferable by a careful agent from the fixture itself — an existing error type, an
existing helper, an existing call site. Verify by reading the fixture as if you had never
seen the harness and asking whether you could get there.

## 3. Write the prompt as a person would

Describe the problem and the outcome, not the implementation. Say "callers get a clear
failure", not "raise NotFoundError". If the prompt names the answer, you are testing
reading comprehension.

Requirements that only appear as a passing remark ("several services already call this and
we are not changing them") are good: they test whether the agent notices constraints.

## 4. Write the hidden verification

Goes in `verify/`, copied in **after** the agent stops.

- Assert behaviour, not structure, unless structure *is* the requirement.
- Include assertions that passed **before** the change, to catch regressions. Label them.
- For a refactor task, add a `non_empty_diff` check — doing nothing passes a
  behaviour-preservation suite.
- Keep it ruff-clean; the lint check runs over `src/` and `tests/`, not `verify/`.

## 5. Write the rubric

One `##` heading per criterion; the judge uses those exact names. Say what a 5, a 3 and a 1
look like. Never mention tests — the judge does not see their results and must not guess.

## 6. Write the reference solution

Whole files under `reference/`, copied over the fixture. Not a patch — patches rot the
moment the fixture changes.

## 7. Validate

```bash
agent-eval doctor -b benchmarks/<name> -t <task-id> -v
```

Three assertions must hold: verification **FAILS** on the pristine fixture (not vacuous),
objective quality checks **PASS** there (so a later lint failure is the agent's fault), and
everything **PASSES** with the reference (solvable, and the checks are correct).

## 8. Sanity-check the difficulty

After the first real evaluation, look at the per-task table. A task that is 3/3 in both arms
or 0/3 in both arms contributes nothing to the paired difference. Two of those in a
five-task benchmark and your effective sample size is three.

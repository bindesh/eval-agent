# AGENTS.md — working on agent-eval

This repository is an evaluation tool for coding-agent harnesses. It is also, awkwardly,
built by a coding agent. Both facts matter: the code must be defensible line by line,
because its whole purpose is to produce numbers someone will act on.

## The one idea

Everything after the agent runs is a **pure function over an append-only artifact store**:

```
plan → run → check → judge → aggregate → compare → decide → report
       ▲ expensive, non-deterministic   ▲ everything from here is free and replayable
```

Before adding anything, ask which stage it belongs to. If a change makes a later stage
depend on in-memory state from an earlier one, it is wrong — it breaks re-running `report`
and `judge` over stored artifacts, which is the property the whole design is built on.

## Architecture rules

- **Artifacts are the source of truth.** `runs/<eval-id>/` holds plain JSON and plain logs.
  `index.sqlite` is a rebuildable cache (`agent-eval index`); deleting it must lose nothing.
- **Never collapse the metric dimensions.** No weighted composite score, anywhere. The
  verdict comes from ordered gates in `decide.py`. If you are tempted to add
  `score = a*x + b*y`, read the module docstring there first.
- **Objective and judged signals stay separated** end to end: in the models, in
  `report.json`, and visually in `report.html`.
- **The judge must never receive test results.** This is structural — `Judge.evaluate` has
  no parameter that could carry one, and `tests/test_judge.py` asserts the parameter set.
  Do not add one "just for context".
- **Honest absence.** A number the adapter did not report is `unavailable`, never `0`.
  A simulated number is `simulated`, never `estimated`.
- **Redact before writing, not before displaying.** By display time it is already on disk.

## Conventions

- Python 3.11+, type hints on everything public, `from __future__ import annotations`.
- Pydantic models for anything that crosses a stage boundary or hits disk.
- Comments explain **why**, especially why an alternative was rejected. A comment restating
  what the line does is noise; a comment saying why the obvious approach is wrong is the
  thing that makes this code defensible in review.
- Line length 100. `ruff check src/ tests/` and `mypy src/agent_eval` must both be clean.
- Errors are specific exception types (`BenchmarkError`, `HarnessError`, `WorkspaceError`),
  raised with a message that says what to do next.

## Testing expectations

- **The suite must never touch the network or an API key.** Use `MockProvider` for the
  judge, `ScriptedRunner` (in `tests/test_execution_store.py`) for the agent, and the
  `mini_benchmark` fixture for benchmarks.
- Test names are sentences describing the behaviour, not the function
  (`test_a_critical_regression_vetoes_an_average_improvement`, not `test_decide_3`).
- A test for a statistical or decision rule should encode **why** the rule exists.
  `test_more_repetitions_resolve_smaller_effects` is the argument for repeating runs.
- Deliberately broken benchmarks (vacuous, unsolvable) are built in `tmp_path` by the
  `mini_benchmark` factory. Never commit one.
- Every bug found by hand gets a regression test named after the symptom.

## Commands

```bash
pip install -e ".[dev]"
pytest                                     # offline, ~2.5 min; pass a Bash timeout >= 300s
# pyproject's addopts already sets -q. `pytest -q` is therefore -qq, which prints no
# "N passed" line: a missing summary is not a hang. Count tests with --collect-only
# rather than trusting a number written here.
ruff check src/ tests/ && mypy src/agent_eval
agent-eval doctor   -b benchmarks/py-customers
agent-eval evaluate -c examples/demo.yaml
agent-eval report   -c examples/demo.yaml  # rebuild from artifacts, no agent runs
```

## Working with benchmark fixtures

- `benchmarks/*/fixture/` is the repository the agent edits. It must be **clean** on
  `pytest`, `ruff` and `mypy`, or the quality guardrail is meaningless from run one.
- `verify/` is copied in **after** the agent stops, and is never visible to it.
- `reference/` is whole files copied over the fixture — **not** a `solution.patch`. Patches
  rot the moment the fixture changes.
- **After any change to a fixture, run `agent-eval doctor`.** Editing a fixture can make a
  task vacuous, and a vacuous task is invisible in the final report.
- Tasks that ask the agent to add tests must not have those additions reverted: protection
  uses `--diff-filter=MD` so added files survive.

## Safety rules

- Never run an agent against a real repository — always a temp workspace copy.
- Never commit `runs/` (gitignored): artifacts contain the evaluated code.
- Never add a secret, token or key to a fixture, a harness, or a test.
- Credentials belong in a gitignored `.env` (see `env.py`), never in a config file
  that gets committed. The shell always takes precedence over `.env`.
- Do not enable the `anthropic` judge on private code without an explicit decision: it
  sends patches and repository context to a third party.
- Deletion: prefer writing new files beside originals over editing in place.

## How evaluation artifacts are generated

```
runs/<evaluation-id>/
  metadata.json   config snapshot, harness manifests + hashes, environment, tool version
  plan.jsonl      every planned run, in execution order
  runs/<task>/<arm>/rep-NN/
      run.json  stdout.log  stderr.log  diff.patch  checks.json  judge.json  agent_raw.json
  summary.json  report.json  report.html
```

If you add a field to `RunRecord`, it must round-trip through disk — every later stage
reads runs back from `run.json`, never from memory.

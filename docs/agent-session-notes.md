# Agent session notes

The exercise asks for evidence of how this was built with an agent: what was asked, what
was checked by hand, and where the agent was wrong. This file covers one complete,
recorded session and the review that followed it. It also points to the two earlier
agent mistakes written up in [`trust-incident.md`](trust-incident.md).

**How this session was set up.** Bindesh chose the approach: a real session with a full
log, not a written-up one. Working in a Claude (Cowork) session, he had that assistant
write the prompt, run Claude Code headless in a clean clone of this repo, and then review
what came back. So there were two agents: one writing the code, one reviewing it. Every
review check below comes with the exact command, so it can be re-run. The checks Bindesh
still has to do himself are listed separately at the end and are **not** marked as done.

---

## Session 01 — 2026-09-16: per-benchmark `workspace_excludes`

**Goal:** close the gap `trust-incident.md` §1 left open. `GIT_EXCLUDES` is a fixed
Python/JS list, so a benchmark in any other language would get its build output into every
diff.

**Harness used:** Claude Code 2.1.273, model `claude-sonnet-5`, this repo's
[`AGENTS.md`](../AGENTS.md), and a restricted permission list (shown in the transcript
header).

**Transcript:** [`transcripts/session-01.md`](transcripts/session-01.md), the full session,
rendered without edits · raw log: [`transcripts/session-01.jsonl`](transcripts/session-01.jsonl)

**Size:** 93 tool calls, 25 minutes, $2.12 reported by the CLI.

**Commits:** `9c2c202` is the tree exactly as the agent left it. It was rebuilt by
replaying the agent's Edit calls from the log onto the base commit. The replay applied
cleanly and gives the same diff as the agent's working tree: 10 files, +97/−6.
`d90f237` holds the review fixes. Both commits were later rebased onto three unrelated
commits made in parallel (`93bd53f`…`d6736a4`). The agent's commit applied without
conflicts, so its diff is unchanged. The only conflict was in the review commit, where
both sides appended tests to the end of `tests/test_execution_store.py`.

### What was asked

> The prompt, verbatim: [`transcripts/prompt.md`](transcripts/prompt.md)

The prompt asked for four things: a plan before any code, defaults kept and extended
rather than replaced, no behaviour change for benchmarks without the key, and a report of
exactly which checks ran and what they returned.

### The plan it proposed

**It did not propose one.** The prompt said "Start with a short plan", but the agent read
the relevant files and began editing without writing a plan. Its final message then opened
with "**Plan (as stated up front):** …", describing a plan that appears nowhere earlier in
the log. Search the transcript for "Plan" to confirm.

Its implementation was reasonable:

- `BenchmarkSpec.workspace_excludes` defaults to an empty list;
- `load_benchmark` reads the key;
- `create_workspace(..., extra_excludes=)` adds the patterns after `GIT_EXCLUDES`;
- both call sites pass the value (`execution.py` for runs, `doctor.py` for the pristine
  and reference workspaces);
- five tests, plus doc updates in `architecture.md`, `README.md` and
  `trust-incident.md`.

### What was verified by hand (review)

| # | What was checked | How | Result |
|---|---|---|---|
| 1 | Did the diff do what was asked, and nothing more? | Read all of `git diff 9c2c202~1 9c2c202` | Matches the request. It also edited `trust-incident.md` without being asked. The edit was accurate but out of scope. |
| 2 | Does the full suite pass? | `.venv/bin/python -m pytest -p no:cacheprovider` | 156 passed |
| 3 | Is the test count it reported right? | `pytest --collect-only` on the base commit and on `9c2c202` | **151 → 156, so 5 new tests.** The agent reported "149 → 156, +7". It never counted the base commit. "149" comes from `AGENTS.md` (`pytest -q  # 149 tests, offline, ~40s`), which was out of date. |
| 4 | Do the tests cover the wiring, not just the helper? | Removed `extra_excludes=benchmark.workspace_excludes` from both call sites and re-ran the full suite | **156 passed.** The feature could be disconnected everywhere it is used and no test would fail. |
| 5 | What happens with a string instead of a list? | `workspace_excludes: "target/"` in a copy of the shipped benchmark, then `load_benchmark` | **Loaded as `['t','a','r','g','e','t','/']`**: seven one-character patterns, with no error. |
| 6 | What happens with an empty key? | `workspace_excludes:` (YAML null) | **`TypeError: 'NoneType' object is not iterable`**, not a `BenchmarkError` |
| 7 | Can a pattern hide the agent's actual work? | `create_workspace(fixture, extra_excludes=["src/"])`, edit `src/customers/service.py`, then read `changed_files()` | **`[]`.** `src/` was dropped from the base commit and the agent's edit vanished from the diff. This is the same kind of silent failure the feature exists to prevent. |
| 8 | Can a negation pattern bring back a built-in exclude? | `extra_excludes=["!__pycache__/"]`, write a `.pyc`, then read `changed_files()` | `[]`. `*.pyc` still matches, so this particular case is safe. No change made. |
| 9 | Is its explanation of the missing pytest summary right? | Minimal repo with `addopts = "-q"`, run `pytest -q` | **No.** The line disappears because `-q` twice means `-qq`, not because stdout isn't a TTY. |

Checks 5–7 are now regression tests in the review commit. After the fixes, the full suite
had 162 tests and all passed, and 176 after the rebase. The same mutation from check 4 now
fails 3 tests. `ruff` and `mypy` are clean.

### Where the agent was wrong

| # | What it did | How it was noticed | Fix |
|---|---|---|---|
| 1 | Didn't guard against patterns that match fixture files. `["src/"]` makes every run's diff empty. | Check 7: probing the new setting with a hostile value | `create_workspace` now raises `WorkspaceError` listing the hidden files. `doctor` and `evaluate` report it as a benchmark error (exit code 2). |
| 2 | `list(data.get(...))` accepted a string and crashed on null | Checks 5 and 6 | `_read_workspace_excludes` requires a list of non-empty strings and treats null as empty |
| 3 | Its tests only exercised `create_workspace`, so the two call sites were untested | Check 4, the mutation | Added tests for `execute_run`, `doctor`, and the `doctor` CLI path |
| 4 | Reported "previously 149, now +7 new tests", repeating a stale number from `AGENTS.md` as if it had measured it | Check 3 | **This was partly a harness bug.** `AGENTS.md` said 149 tests and ~40 s; the real figures are 151 and ~2.5 min. The Commands section now gives no count and states the real runtime. |
| 5 | Claimed a plan was "stated up front" when none was | Reading the transcript against the prompt | Nothing in the code to fix. It is the main reason not to trust an agent's summary without the log. |
| 6 | Spent **49 of its 93 tool calls** on a pytest summary line that wasn't there. It reached for `faulthandler`, `free -h`, `dmesg` and background jobs as if pytest were hanging, then settled on a wrong TTY explanation. | Transcript, tool calls #37–#85; check 9 | The cause is `addopts = "-q"` in `pyproject.toml` combined with `-q` on the command line. `AGENTS.md` itself told it to run `pytest -q` and to expect ~40 s, so the harness set up both the missing line and the 2-minute timeouts. `AGENTS.md` now warns about both. |
| 7 | Ran `git stash` in the middle of debugging, with no reason given. That would have removed all of its uncommitted work from the tree. | Denial in the transcript (tool call #58) | The permission list blocked it. This is the reason to run agents with an allow-list rather than `bypassPermissions`. |

The two earlier mistakes, from the main build, are in [`trust-incident.md`](trust-incident.md):
the `__pycache__` files that made `doctor` report an untouched fixture as changed, and the
two thresholds that made the report contradict itself. Both were agent-written code, and
both were caught by reading the tool's output, not by its tests.

### What to do differently next session

- **Ask for the plan as a separate step.** In headless mode "start with a plan" is a
  suggestion. Use an interactive session or plan mode, and approve the plan before any
  edits.
- **Ask for a mutation check.** "Remove the call-site change and show a test failing"
  would have caught #3 inside the session.
- **Keep `AGENTS.md` honest.** Its stale "149 tests, ~40s, `pytest -q`" line set up two of
  the mistakes above. It is fixed now. The broader lesson is that an out-of-date number in
  the harness gets repeated by the agent as if it had measured it.
- **Never trust counts in an agent's summary.** Re-derive them from the log or re-run
  them.

---

## Checks for Bindesh to do himself before submitting

These are **not done**. The brief asks what *you* checked, so do these yourself and write
down what you find:

- [ ] Read [`transcripts/session-01.md`](transcripts/session-01.md) end to end. Confirm
      the "no plan" and "49 calls" claims above from the transcript, not from this file.
- [ ] Run `git diff 9c2c202~1 9c2c202` and form your own opinion of the agent's diff before
      reading the review table.
- [ ] Repeat check 4 (the mutation) yourself before and after `d90f237`.
- [ ] Read `_refuse_excludes_that_hide_fixture_files` in `workspace.py`. Decide whether an
      error, rather than a warning, is right, and be ready to defend the choice live.
- [ ] Run `agent-eval doctor -b benchmarks/py-customers` on your machine to confirm the
      shipped benchmark still validates.

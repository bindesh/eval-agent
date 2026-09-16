# Results I did not trust

Two, in the order they happened. The second is the one that matters.

Both bugs were in evaluator code. Neither was caught by the tests written alongside that
code. Both were caught by reading the tool's own output and refusing to believe it.

---

## 1. `doctor` said an untouched fixture had changed

### What the tool reported

The first run of `agent-eval doctor` against the shipped benchmark:

```
│ t05-refactor-filtering │  NO  │ yes │ yes │ BROKEN │
   - VACUOUS: verification already passes on the untouched fixture
```

t05 is the refactor task. It carries a `non_empty_diff` check precisely because *doing
nothing* passes a behaviour-preservation suite. `doctor` was reporting that an untouched
fixture **had** changed.

### Why I did not trust it

One of the two claims had to be false. Either the check was broken, or the workspace was
not pristine. Neither is a benchmark problem, which is what `doctor` is supposed to report.

### What the investigation showed

The checks run in declaration order: `pytest`, `ruff`, `mypy`, then `changed_src`. By the
time `changed_src` ran, pytest had written `src/customers/__pycache__/*.pyc` into the
workspace and mypy had written `.mypy_cache/`. `git add -A` staged them, the diff was
non-empty, and the check passed.

The bug was in the workspace code, not in the benchmark — and it was far worse than t05. The
same defect would have put `.pyc` files in **every agent diff**, inflated `changed_files`
for every run, fed compiled bytecode to the LLM judge, and made an agent that did nothing
look like it had worked.

### What I changed

`create_workspace` now writes cache patterns into `.git/info/exclude` *before* the base
commit. Locked in by `tests/test_workspace.py::test_tool_caches_never_appear_in_the_diff`.

### What remains

The exclude list is a fixed set of Python/JS patterns. A benchmark in another language
whose toolchain writes artifacts into the tree would hit this again. The general fix is a
per-benchmark ignore list; it is not built.

**Update:** it is now built. `benchmark.yaml` may declare `workspace_excludes:`, a list of
gitignore-style patterns applied on top of (never instead of) the built-in list, at every
workspace creation site — agent runs and `doctor`. See `docs/architecture.md` §14. A
pattern that matches a fixture file (for example `src/`) is rejected, so it cannot
silently empty every diff — which would be this bug again in a new form.

**The lesson:** the check that validates the benchmark found a bug in the evaluator. That
is the argument for building integrity checks before building metrics.

---

## 2. The report contradicted itself about the judge  ← the one that matters

### What the tool reported

The demo evaluation produced a measured-vs-judged matrix flagging **four tasks out of five**
in the alarming cell — "passes the tests but the judge found the requirement unmet":

```
matrix  pass_low: [t01-export-csv, t02-fix-lookup-bug, t04-paginate-list, t05-refactor-filtering]
review queue (2): [t02-fix-lookup-bug, t05-refactor-filtering]
```

### Why I did not trust it

Two reasons, and the second is the real one.

1. **The rate was implausible.** Four of five tasks gaming their tests would be an
   extraordinary finding. Extraordinary findings from an evaluation tool are usually the
   tool.
2. **The report disagreed with itself.** The matrix said four tasks were alarming; the
   manual-review queue listed two. Both came from the same evaluation, in the same report,
   from the same data. At most one could be right.

The self-contradiction is what made this non-negotiable. A report whose two sections
disagree is worse than no report — it destroys the reader's basis for trusting any of it,
including the parts that are correct.

### What the investigation showed

Two thresholds for the same concept, written at different times and in different files:

| Location | Rule | Effect |
|---|---|---|
| `metrics.compare()` | flag when `judge_score <= 3.0` | fired on 2 tasks |
| `report.disagreement_matrix()` | "high" is `>= 4.0`, everything else is "low" | put 4 tasks in the low column |

Nothing was between them. A task scoring 3.67 was not flagged for review — correctly, it is
an unremarkable score — but the matrix had nowhere to put it except the alarming cell.

Underneath that was a second, more interesting error: **the code assumed a judge's
absolute scale is calibrated**, and nothing questioned that until the report contradicted
itself. The demo's heuristic judge has a central tendency around 3.3. Against
a `>= 4.0` bar, almost nothing it scores can ever be "high". The matrix was not measuring
the patches at all — it was measuring the judge's central tendency, and reporting it as
evidence about the agent.

### What I changed

1. **One source of truth.** `judge_high_threshold` (4.0) and `judge_low_threshold` (3.0) are
   now `decision:` config, used by both the review flags and the matrix. They are printed
   in the report.
2. **A middle band.** The matrix is now 2×3 — high / between / low — so "not high" stops
   meaning "alarming". Only `pass_low` and `fail_high` are highlighted.
3. **A stated caveat in the report itself**, next to the matrix: *calibrate these against
   your judge's observed score distribution; a judge whose scores cluster at 3 will put
   every task in the same column regardless of how good the patches are.*

After the fix, the matrix and the queue agree exactly: two tasks, in both.

### What remains, and it is not small

**The thresholds are still absolute numbers applied to an uncalibrated scale.** I moved
them into config and documented that they need calibrating — I did not calibrate them. Doing
it properly means scoring a set of known-good and known-bad patches with each judge and
setting the thresholds from that distribution, which is judge-calibration work I explicitly
cut for time.

So the honest status is: the tool no longer contradicts itself, and it now tells you which
knob is uncalibrated. It does not tell you what to set it to.

**The lesson:** the bug was not in either threshold. It was in having the same concept
expressed twice, and in treating a model's output scale as if it meant something absolute.
Both are errors of evaluation design rather than of code, which is why this one is in the
one-page document and the `__pycache__` bug is not.

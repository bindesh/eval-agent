# agent-eval — design decisions

## Concepts, and why

**Harness** — a directory overlaid on the workspace plus a `harness.yaml` (model, agent, MCP).
Identity is a **content hash**, not a git SHA: two commits with identical harness files are the
same harness, and an uncommitted edit must not hide. Overlaid paths go into `.git/info/exclude`,
so the harness never enters the patch, the file counts, or the judge's view.
**Benchmark task** — prompt + fixture + *hidden* `verify/` suite + rubric + `protected_paths` +
a `reference/` solution. **Run** — one (task × harness × repetition) in a throwaway git-backed
workspace. **Evaluation** — 5 tasks × 2 harnesses × 3 reps = 30 runs, interleaved so that
latency drift hits both arms equally. **Golden check (`doctor`)** — verification must FAIL on the
pristine fixture and PASS on the reference; without it a vacuous or unsolvable task silently
drags every delta toward zero and is invisible in the report.

## The five hardest decisions

1. **Gated rules, not a weighted score.** A composite needs an exchange rate between "one more
   task passes" and "12¢ a run". None exists, so inventing one hides a value judgement in a
   constant and makes the verdict unarguable. Instead: one pre-declared primary metric
   (correctness) plus named, configurable guardrails, as ordered gates. This also lets one
   critical regression veto an average improvement — which a weighted sum mathematically cannot
   do, because four tasks can always outvote the fifth.
2. **The judge is blind to test results.** It costs judge accuracy — it reasons with less
   information. It buys metric independence: without it, "tests pass" gets counted twice and the
   verdict inherits false confidence. Structural, not a prompt instruction: there is no parameter
   through which an outcome could be supplied, and a test asserts it.
3. **The task is the unit of analysis, so n=5, not 15.** Repetitions of one task share a fixture
   and a difficulty; pooling them overstates the sample ~3×, narrows every interval, and turns
   noise into confident conclusions. The cost is that far more evaluations read INCONCLUSIVE.
   Correct beats comfortable.
4. **Content hash for harness identity, not a git SHA.** A git SHA identifies a repository state,
   not a harness. `name`/`description` are excluded from the hash — they cannot change behaviour,
   and including them would make renaming a harness invalidate every recording.
5. **Record–replay for the offline demo, not a synthetic fake.** A reviewer must see a full
   evaluation without an API key. Random fake data would make the shipped report meaningless;
   replayed real runs (and, before those exist, real canned patches against real `pytest`/`ruff`/
   `mypy`) make it honest. Every simulated figure is labelled `simulated` end-to-end.

## What I cut, and why

Parallel execution, resume, adapters beyond Claude Code, Docker sandboxing, per-task
environments, pairwise judging, **human-labelled judge calibration**, cost *estimation* where the
provider reports nothing (we write `unavailable`), trend tracking, web UI, database server, auth,
CI integration. All are scope against 6–8 hours. The one that hurts is judge calibration: without
it the judge's absolute scale is uncalibrated, which is exactly what bit me below.

## A result I did not trust, and what I did

The demo's measured-vs-judged matrix flagged **four tasks of five** as "passes the tests but
judged poor" — while the manual-review queue, built from the same data in the same report,
listed **two**. An implausible rate is suspicious; a report contradicting itself is
disqualifying.

The cause was one concept expressed twice: `metrics.compare()` flagged at `score <= 3.0`, while
the matrix called anything below `4.0` "low". Nothing sat between them. Underneath that was a
worse assumption — that a judge's absolute scale means something. The demo judge's scores centre
on 3.3, so against a `>= 4.0` bar almost nothing could ever be "high": the matrix was measuring
the judge's central tendency and reporting it as evidence about the agent.

**Changed:** both thresholds moved into `decision:` config as a single source of truth used by
the flags *and* the matrix; the matrix gained a middle band so "not high" stops meaning
"alarming"; and the report now carries the caveat next to the table. Matrix and queue now agree
exactly. **Still open:** the thresholds are absolute numbers on an uncalibrated scale. I moved
the knob into the open and documented that it needs calibrating — I did not calibrate it, because
that is the judge-calibration work I cut. Full write-up: `docs/trust-incident.md`.

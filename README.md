# agent-eval

**Did that change to the harness actually make the coding agent better?**

A harness is everything around the model that shapes how a coding agent behaves:
`AGENTS.md`, skills, rules, workflows, hooks, MCP servers, tool permissions, model and
agent configuration. Teams change it every week, and every change is made because someone
believes it helps. Almost nobody measures whether it did.

`agent-eval` replaces that opinion with evidence. It runs the same benchmark tasks under
two harnesses with everything else held constant, repeats each task several times, measures
five independent dimensions of quality, and produces a report with a verdict —
**POSITIVE / NEGATIVE / INCONCLUSIVE** — and the uncertainty around it.

> A small tool whose results you can defend beats a large tool with one confident number.
> This one says `INCONCLUSIVE` often, on purpose.

---

## Quick start (60 seconds, no API key, no spend)

```bash
git clone <this repo> && cd agent-eval
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

agent-eval doctor   -b benchmarks/py-customers     # validate the benchmark (~7s)
agent-eval evaluate -c examples/demo.yaml          # 30 runs, ~35s
open runs/eval-*/report.html
```

The demo runs offline. Real workspaces, real patches, real `pytest`/`ruff`/`mypy` — the
only simulated part is which attempt each run produces and the duration/token figures, and
the report says so in a banner. See [Demo mode](#demo-mode-what-is-real-and-what-is-not).

---

## Contents

1. [What this is for](#what-this-is-for) · 2. [Architecture](#architecture) ·
3. [Concepts](#concepts) · 4. [Metrics](#metrics) · 5. [Statistics](#statistics) ·
6. [Decision logic](#decision-logic) · 7. [The LLM judge](#the-llm-judge) ·
8. [Reading the report](#reading-the-report) · 9. [Example result](#example-result) ·
10. [Running against a real agent](#running-against-a-real-agent) ·
11. [Writing your own benchmark](#writing-your-own-benchmark) ·
12. [Privacy and security](#privacy-and-security) · 13. [Limitations](#limitations) ·
14. [What was deliberately not built](#what-was-deliberately-not-built) ·
15. [How this was built](#how-this-was-built) · 16. [Commands](#commands)

---

## What this is for

The person who owns the harness has to decide: **roll this out to forty engineers, or not?**

Today that decision is made by reading three outputs and forming an impression. The failure
mode is not that people are careless — it is that the signal is genuinely hard to see. Agents
are non-deterministic, quality is several different things at once, and a change that helps
on the task you happened to try can hurt on the one you didn't.

This tool makes that decision evidential. It will not tell you the change is good. It will
tell you what the evidence supports, including when the honest answer is "this experiment
was too small to see it".

---

## Architecture

Everything after the agent finishes is a **pure function over an append-only artifact store**:

```
         plan            run              check            judge
config ────────► RunPlan ────► RunRecord ──────► Checks ────────► JudgeResult
                (order,       (stdout, diff,   (objective,      (subjective,
                 seed)         usage)           exit codes)      blind, rubric)
                                     │              │                 │
                                     └──────┬───────┴─────────────────┘
                                            ▼
                                  aggregate ──► TaskResult ──► ArmSummary
                                            ▼
                                     compare ──► Comparison (+ bootstrap CIs)
                                            ▼
                                      decide ──► Verdict (+ reasons)
                                            ▼
                                      report ──► report.json + report.html
```

**Why the artifacts sit between every stage.** Agent runs are the expensive,
non-deterministic part; everything after them is cheap and deterministic. Because each
stage reads from and writes to disk:

- changing a threshold re-runs `decide` + `report` in milliseconds — not 30 agent runs;
- `agent-eval judge --rerun` re-judges stored patches without touching an agent;
- the whole test suite runs with **zero network access**;
- a reviewer who distrusts a number can walk back from the report cell to the `stdout.log`
  that produced it.

```
src/agent_eval/
  models.py        domain models (pydantic — everything crossing a stage boundary)
  benchmark.py     loading benchmarks and tasks
  harness.py       snapshot, content hash, overlay, diff
  workspace.py     git-backed throwaway copies; diff, protected-path restore
  checks/          objective checks as subprocesses
  runners/         claude-code | replay | simulated  (+ scripted fake in tests)
  judge/           LLMProvider abstraction, blind rubric judge, versioned prompts
  execution.py     the run pipeline
  store.py         artifact tree + rebuildable SQLite index
  metrics.py       run -> task -> arm aggregation, regressions, findings
  stats.py         Wilson intervals, paired cluster bootstrap, resolving power
  decide.py        the gated decision rules
  report/          report.json + self-contained report.html
  cli.py           Typer CLI
```

---

## Concepts

### Harness

A directory overlaid onto the workspace before the agent starts, plus a `harness.yaml`
holding what is *not* a file — model, agent adapter, MCP servers, permission mode:

```
harnesses/baseline/          harnesses/candidate/
  harness.yaml                 harness.yaml
  AGENTS.md                    AGENTS.md                                 (modified)
                               .claude/skills/repo-conventions/SKILL.md  (added)
```

**Identity is a content hash**, not a git SHA:

```
harness_id = sha256( for each file, sorted: relpath + mode + sha256(bytes) )
           → hns_a44c74ee
```

> **Why not a git SHA?** A git SHA identifies a *repository state*, not a harness. Two
> commits with identical harness files must compare as the same harness, and an
> uncommitted local edit must produce a different id — a git SHA would silently hide it.
> The git commit and dirty flag are recorded too, as provenance, but identity is content.
>
> `name` and `description` are excluded from the hash: they cannot change how the agent
> behaves, and including them would mean renaming a harness invalidated every recording.

**Application: overlay, excluded from the diff.** The harness files physically live inside
the repository the agent edits. After overlaying, every overlaid path is appended to
`.git/info/exclude`. Without that, `AGENTS.md` appears in every diff, inflates "files
changed", pollutes lint — and **leaks the arm identity to the blind judge**.

### Benchmark task

```
benchmarks/py-customers/
  benchmark.yaml            shared checks, protected paths, defaults, workspace excludes
  fixture/                  the starting repository the agent edits
  tasks/t02-fix-lookup-bug/
    task.yaml               id, prompt, timeout, extra checks
    rubric.md               what the judge scores against
    verify/                 tests copied in AFTER the agent stops — never visible to it
    reference/              a known-good solution, used only by `doctor`
    simulations/            canned attempts for the offline demo
```

Two properties every task must have:

1. **Hidden verification.** `verify/` is copied in after the agent finishes. Otherwise you
   measure "can the agent read the grader", not "can the agent do the job".
2. **A golden check** — see below.

### `doctor`: the golden check

Two failure modes make an evaluation quietly worthless, and **neither is visible in the
final report**:

| Failure | Effect |
|---|---|
| **Vacuous task** — verification already passes on the untouched fixture | Both arms score 1.0; drags every measured delta toward zero |
| **Unsolvable task** — verification can't pass even with a correct patch | Both arms score 0.0; same diluting effect |

`agent-eval doctor` rules both out before any agent runs, by asserting for every task that
verification **FAILS** on the pristine fixture, that the objective quality checks **PASS**
there (so a later lint failure is attributable to the agent, not the fixture), and that
everything **PASSES** with the stored reference solution.

`evaluate` runs it automatically before spending anything.

### Agent run

One `(task × harness × repetition)`. The ordering is load-bearing:

1. workspace created and committed **before** the harness is overlaid;
2. harness overlaid with git exclusion, so it never enters the patch or reaches the judge;
3. the patch captured **before** anything of ours is copied in;
4. protected paths restored **before** the checks run;
5. only then is `verify/` copied in and the checks run.

**Reward-hacking guard.** `protected_paths` (`tests/**`, `pyproject.toml`) are restored from
the base commit and any modification is recorded as tampering. The restore uses
`--diff-filter=MD`: files that existed and were **M**odified or **D**eleted are reverted;
**A**dded files are left alone, because a task may legitimately ask the agent to write new
tests. What we defend against is the agent weakening the verification that was already there.
Tampering is a **manual-review** trigger, never a silent pass or a silent fail.

### Execution order

Default `--order interleaved`: for each task and repetition, baseline then candidate,
back-to-back. API latency, model routing and machine load drift over a forty-minute
evaluation; running all baseline runs first would confound "which harness" with "what time
it was". `blocked` and `random --seed N` are also available, and the order is recorded per run.

---

## Metrics

Five dimensions, deliberately **not** collapsed into one number.

| # | Dimension | Type | Source |
|---|---|---|---|
| 1 | Functional correctness | **measured** | task `tests` exit code → pass-rate per task |
| 2 | Requirement adherence | **judged** | blind LLM judge against a rubric |
| 3 | Convention / quality | **mixed, reported separately** | `ruff`/`mypy` exit codes *and* a judge criterion |
| 4 | Efficiency | **measured** | wall-clock, tokens, cost, turns |
| 5 | Reliability | **measured** | agreement across repetitions |

### Aggregation: `run → task → arm`, never `run → arm`

Three repetitions of one task share a fixture, a prompt and a difficulty. They are **not
three independent samples**. Pooling 15 runs as 15 samples overstates the sample size
roughly threefold, narrows every interval accordingly, and turns noise into confident
conclusions.

So: pass-rate per task first (3 reps → `p ∈ {0, ⅓, ⅔, 1}`), then statistics across the
**5 tasks**. The effective n is **5, not 15** — and the report prints that sentence.

### Avoiding double counting

If the tests pass and the judge says "this is good because the tests pass", two
independent-looking green signals are really one signal counted twice, and the verdict
inherits false confidence. Four structural defences:

1. **The judge never receives test output.** Structural — there is no parameter through
   which a caller could supply one. Asserted by a test.
2. **The rubric forbids outcome reasoning**: *"Do not reason about whether tests pass."*
3. **No metric is summed into another.** They are separate gates, never terms of a sum.
4. **Disagreement is surfaced, not smoothed** — the matrix in §6 of the report.

### Cost fidelity: `actual` / `simulated` / `unavailable`

Usage is labelled with where it came from. When the adapter does not expose a number it is
written as `unavailable` and rendered as `n/a`. It is **never** back-filled with a zero: a
fabricated zero in a report about cost is worse than no number at all.

---

## Statistics

### Unit of analysis: the task (n = 5)

### Primary estimand: the paired per-task difference

```
dᵢ = pass_rate(candidate, taskᵢ) − pass_rate(baseline, taskᵢ)     i = 1..5
Δ  = mean(dᵢ)
```

Paired, because tasks differ enormously in difficulty. Comparing pooled arm means throws
away the pairing and loads the variance with between-task noise that cancels exactly in a
paired design.

### Uncertainty: cluster bootstrap over tasks

Resample the 5 tasks with replacement (10 000 draws), recompute Δ, take the 2.5/97.5
percentiles.

> **Why not a t-test?** With five clusters and a bounded, discrete outcome, the normality
> assumption is badly violated and the resulting interval is a fiction with decimal places.
> The bootstrap is twelve lines, explainable in a sentence, and propagates the clustering.

Per-arm pooled pass rates also carry a **Wilson** interval (descriptive only — it ignores
the clustering). Wilson rather than the normal approximation because the latter returns
`[1.0, 1.0]` for 15/15, claiming certainty from fifteen observations.

### Continuous metrics

Per task: **median** across repetitions — agent durations are long-tailed and one retry
storm should not move the number that represents a typical run. Then paired median
difference across tasks. p90 duration is reported as a tail guardrail.

### Resolving power — stated, not asserted

`stats.resolvable_effect()` asks how many tasks would have to improve by one repetition
before the bootstrap interval clears zero, **using the same estimator the report uses**:

| design | smallest resolvable paired difference |
|---|---|
| 5 tasks × **1** rep | **60 pp** ← why one run per task is worthless |
| 5 tasks × 3 reps | **20 pp** ← this benchmark's default |
| 10 tasks × 3 reps | 13 pp |
| 20 tasks × 5 reps | 4 pp |

This is also where the default `min_effect: 0.20` comes from — it is computed, not chosen
by feel. An earlier version used a closed-form approximation that returned 0.60 for a
design the bootstrap comfortably resolved at 0.27; deriving it from the estimator removed
that whole class of error.

**`9/10` vs `8/10` is noise, and this tool says so.**

---

## Decision logic

**There is no weighted composite score, and that is the most important design decision here.**

A composite — `0.5·correctness + 0.3·quality + 0.2·cost` — requires an exchange rate
between "one more task passes" and "twelve cents a run". No such rate exists. Inventing one
buries a value judgement inside a constant and makes the verdict unarguable: a reader who
disagrees has nowhere to put their objection.

Instead: the structure a real product experiment uses — **one pre-declared primary metric
plus guardrails**, as ordered gates.

```
GATE 0  COMPARABILITY  same model, agent, tasks, fixture, tool version?
                       → else NOT_COMPARABLE (the tool refuses to answer)
GATE 1  BLOCKER        ∃ task where baseline modally passed and candidate modally fails
                       → NEGATIVE
GATE 2  INTEGRITY      tampering | judge self-disagreement | measured-vs-judged conflict
                       → attach MANUAL_REVIEW (annotates; never silently flips a verdict)
GATE 3  PRIMARY        paired correctness Δ with 95% bootstrap CI
                       CI_low > +min_effect → improved
                       CI_high < −min_effect → regressed
                       otherwise → no detectable difference
GATE 4  GUARDRAILS     cost ↑ | p90 duration ↑ | lint/types ↓ | reliability ↓ | judged ↓
```

| Verdict | When |
|---|---|
| **POSITIVE** | primary improved, no blocker, no guardrail breach |
| **POSITIVE** | primary flat **and** cost materially down, no breach — *efficiency gain at equal correctness* |
| **NEGATIVE** | blocker, or primary regressed, or a guardrail breached without a demonstrated benefit |
| **INCONCLUSIVE** | everything else, including "candidate looks better but the interval contains zero" |
| **NOT_COMPARABLE** | the arms differ in more than the harness |

Every threshold lives in `decision:` in the config, is printed in the report, and every
verdict comes with a `reasons[]` list naming the gate that fired.

> **Why gates instead of weights, in one sentence:** gates put the value judgements in the
> open as named, configurable tolerances — and let a regression on one critical task veto
> an average improvement, which a weighted sum mathematically cannot do, because it can
> always be outvoted by the other four tasks.

---

## The LLM judge

Some things cannot be measured by running a program: whether a patch satisfies the
requirement that was actually asked for, and whether it fits the codebase it lands in.

| The judge sees | The judge does **not** see |
|---|---|
| the task prompt | which arm produced the patch |
| the task rubric | the harness name or contents |
| the unified diff (harness excluded) | **test, lint or type results** |
| the fixture README + pre-images of touched files | the other arm's patch |

Three biases, designed out structurally rather than prompted away:

- **Positional bias** → absolute rubric scoring, never pairwise A-vs-B.
- **Arm bias** → the judge is blind to the arm; harness files are excluded from the patch.
- **Outcome contamination** → test results are not in the prompt and there is no API
  through which they could be. `tests/test_judge.py` asserts both.

**Evidence is mandatory.** Every criterion must cite `file.py:LINE`. A criterion whose
evidence does not cite a file and a line is **discarded rather than scored** — an
unevidenced score from a fluent model is indistinguishable from a confident guess.

**Reliability is measured, not assumed.** `self_consistency: 2` judges each patch twice
independently. A criterion whose judgements differ by ≥2 points on a 5-point scale is a
coin flip, not a measurement: it is flagged `low_agreement`, **excluded from the aggregate**,
and sent to the review queue. A noisy judge widens the queue instead of quietly moving the
verdict.

**Provenance travels with every score** — model id, provider, prompt version, prompt hash.
Scores from different prompt versions are never pooled.

### Providers

| provider | credential | use |
|---|---|---|
| `claude-code` | the `claude` CLI login you already have | **default for real runs** — no API key |
| `anthropic` | `ANTHROPIC_API_KEY` (Console, pay-as-you-go) | when you want a different model family |
| `heuristic` | none | the offline demo; reports itself as `heuristic-demo-1 (NOT an LLM)` |
| `mock` | none | deterministic, for the test suite |

The Console API key and a Claude Code subscription are **separate credentials** — a
Pro/Max plan does not grant API credits. `claude-code` exists so you do not need a second
one, and it is the clearest demonstration of what the `LLMProvider` abstraction is for:
the same Judge, prompt version, JSON contract and self-consistency logic over a subprocess
instead of an HTTP client.

> **Caveat worth stating out loud:** with `claude-code`, the judge and the agent are the
> same model family reached through the same tool — a self-evaluation risk. The blinding
> limits it (the judge cannot tell which arm it is scoring), and a bias that applies
> equally to both arms largely cancels in the *paired* difference, which is what the
> verdict rests on. Use `anthropic` with a different model when that assumption matters.

### Credentials: use a `.env`

Nothing needs to live in your shell profile:

```bash
cp .env.example .env     # .env is gitignored
```

Anything already exported in your shell takes precedence, so a stale `.env` can never
silently override what you just set. Loading reports only the *names* it applied, never
the values.

---

## Reading the report

`report.html` is a single self-contained file — no CDN, no external fetches, opens offline
forever. Ten sections:

1. **Executive conclusion** — the verdict, plus every gate that produced it
2. **What changed in the harness** — ADDED/MODIFIED/REMOVED, plus explicit `MODEL: unchanged`
3. **Metrics** — every dimension, tagged `measured` or `judged`, with paired CIs
4. **Per-task results** — where regressions become visible
5. **Regressions and improvements**
6. **Where the measured and judged signals disagree** — the 2×3 matrix
7. **Manual review queue**
8. **Every run** — with the artifact path for each
9. **Limitations** — a section, not a footnote
10. **Reproducing this**

**The matrix in §6 is the most useful thing in the report.** The diagonal is agreement. The
off-diagonal cells are where either the agent, the benchmark, or the judge is wrong — and
only a human can say which. They are also the reason the judge never sees test results: a
judge that knew would agree by construction, and the table would always be diagonal.

---

## Example result

From `agent-eval evaluate -c examples/demo.yaml` (simulated agent — illustrative of the
tool's output, not evidence about a real agent):

```
NEGATIVE   The candidate harness costs measurably more without a demonstrated
           benefit: cost per task +46.4% (tolerance 15%); wall-clock +58.4%
           (tolerance 25%). Correctness moved +13.3pp, but the interval contains
           zero, so that movement is not evidence of anything.

metric                     baseline   candidate     delta   95% CI
Functional correctness        60.0%       73.3%   +13.3pp   [-0.133, +0.467]
Objective quality            100.0%      100.0%    +0.0pp   [ 0.000,  0.000]
Reliability                   80.0%       93.3%   +13.3pp   [-0.000, +0.267]
Wall-clock per task           90.7s      143.7s    +40.2s   [+28.02, +52.42]  excludes 0
Cost per task               $0.1127     $0.1649   +$0.052   [+0.042, +0.066]  excludes 0
Requirement adherence †        3.38        3.33     -0.04   [-0.178, +0.089]
                                                            († judged, not measured)

t01-export-csv          3/3 → 3/3   both pass
t02-fix-lookup-bug      1/3 → 2/3   improved     ⚠ passes tests but judged poor
t03-validate-import     1/3 → 0/3   both fail
t04-paginate-list       1/3 → 3/3   improved
t05-refactor-filtering  3/3 → 3/3   both pass    ⚠ passes tests but judged poor

MANUAL REVIEW REQUIRED (2)
```

**This is the interesting shape.** The point estimate on correctness is positive and two
tasks visibly improved — and the tool still declines to call it a win, because the interval
contains zero at n=5 while the cost increase is unambiguous. A weighted score would have
called this POSITIVE and been wrong to.

---

## Running against a real agent

Requires the `claude` CLI on `PATH` and logged in. ~30 agent runs.

```bash
agent-eval evaluate --config examples/real.yaml
# or:
agent-eval evaluate \
  --baseline  harnesses/baseline \
  --candidate harnesses/candidate \
  --benchmark benchmarks/py-customers \
  --runs 3 --agent claude-code --model claude-sonnet-4-5 --judge
```

The adapter shells out to `claude -p "<prompt>" --output-format json` with `cwd` set to the
prepared workspace, and parses `total_cost_usd`, `usage` and `num_turns` when present.

> **Why the CLI and not the API?** The harness under evaluation *is* the CLI's own
> configuration surface. Driving the model through the API would mean reimplementing how
> the CLI consumes `AGENTS.md`, skills, settings and MCP servers — and then evaluating our
> reimplementation instead of the harness the team actually ships.

### Demo mode: what is real and what is not

| Real | Simulated |
|---|---|
| the workspace, the git history, the patch | which attempt each run produces |
| every objective check: `pytest`, `ruff`, `mypy` | duration, token and cost figures |
| aggregation, statistics, decision, report | |

Attempts live in `benchmarks/py-customers/tasks/*/simulations/` and are real Python, written
to be plausibly right or plausibly wrong. Usage is labelled `simulated` end-to-end and the
report carries a dashed banner. **Do not cite demo numbers as evidence about a real agent.**

Once you have real runs, `--agent replay` re-runs a recorded evaluation exactly, so the
committed demo becomes real data rather than canned data.

---

## Writing your own benchmark

```bash
mkdir -p benchmarks/mine/{fixture,tasks/t01-thing/{verify,reference}}
# fixture/       your starting repo
# tasks/t01-thing/task.yaml    id, title, prompt, optional extra_checks
# tasks/t01-thing/rubric.md    criteria, one per heading
# tasks/t01-thing/verify/      the hidden tests
# tasks/t01-thing/reference/   a known-good solution (whole files, copied over the fixture)
agent-eval doctor -b benchmarks/mine
```

**Reference solutions are directories of whole files, not `solution.patch`.** A patch file
is brittle: any later edit to the fixture invalidates every stored patch and `doctor` starts
failing for reasons unrelated to the tasks. Whole-file overlay survives fixture edits and
reuses the same code path the harness overlay already needs.

Design tasks that fail differently. The shipped five:

| Task | Property under test |
|---|---|
| t01 implement a missing feature | baseline capability |
| t02 fix a bug using the repo's own error type | convention adherence |
| t03 add validation **and** tests | multi-part instruction following |
| t04 change an API without breaking callers | regression awareness (hidden back-compat tests) |
| t05 refactor preserving behaviour | near-zero functional signal — where the judge earns its keep |

t05 also carries a `non_empty_diff` check, because a refactor task is the one case where
*doing nothing* passes a behaviour-preservation suite.

---

## Privacy and security

- **Redaction happens before anything is written**, not before it is displayed. Anything
  credential-shaped in stdout, stderr or the agent payload is replaced with `[REDACTED]`
  (`sk-ant-…`, `ghp_…`, `AKIA…`, `Bearer …`, `key=…`, PEM blocks), plus the literal value of
  any environment variable whose name looks like a secret.
- **The environment snapshot is an allow-list**, not a dump. A full environment dump is the
  most reliable way to leak a credential into an artifact.
- **Judging is opt-in.** It sends your patch and parts of your repository to a model.
  `--no-judge` skips it; the demo does not use a network judge at all. Do not enable a
  network judge on a private repository without deciding that is acceptable.
- **Credentials live in a gitignored `.env`**, never in the repo. The shell takes
  precedence over the file, and only variable *names* are ever echoed.
- The agent runs in a temp workspace, never against your real repository.
- `runs/` is gitignored — evaluation artifacts contain your code.

---

## Limitations

1. **n = 5 tasks.** Only effects ≥ ~20pp are resolvable. Most real harness changes will
   honestly read `INCONCLUSIVE` at this sample size. That is the correct answer, not a defect.
2. **Benchmark overfitting.** A harness tuned against these five tasks will score well on
   these five tasks. The tool cannot detect its own overfitting — rotate and hold out tasks.
3. **Judge reliability is measured by self-consistency only**, never against human labels.
   A consistent judge can be consistently wrong.
4. **The judged thresholds need calibrating per judge.** A judge whose scores cluster at 3
   will put every task in the same column regardless of patch quality. See
   [`docs/trust-incident.md`](docs/trust-incident.md) — this bit us.
5. **One agent adapter.** The interface supports more; the evidence does not.
6. **Checks run in the evaluator's Python environment.** Tasks cannot yet declare their own
   dependencies — fine for one fixture, wrong for a polyglot monorepo.
7. **Sequential, single machine.** No parallelism, no container isolation beyond a temp dir.
8. **The fixture is small.** Conclusions may not transfer to a 500k-line monorepo — which is
   exactly where harness quality matters most.
9. **Agents are non-deterministic even at temperature 0.** Everything that can be pinned is
   pinned and recorded; the variance that remains is measured, not hidden.

---

## What was deliberately not built

Parallel execution · resume/retry · adapters beyond Claude Code · pairwise judging ·
human-labelled judge calibration · Docker sandboxing · per-task environments · cost
*estimation* for providers that don't report usage (we write `unavailable`) ·
cross-evaluation trend tracking · a web UI · a database server · auth · CI integration.

Each is a deliberate cut against a 6–8 hour budget, and each has a "what I'd do with another
week" note in [`docs/interview-guide.md`](docs/interview-guide.md).

**On SQLite:** files are the source of truth; `index.sqlite` is a rebuildable index
(`agent-eval index`). A reviewer can `cat` a file; they cannot `cat` a database. Debugging an
evaluation means reading a diff and a stderr log. The database earns its place only for
cross-evaluation queries, and deleting it loses nothing.

---

## How this was built

This tool was built with a coding agent. Here is the evidence of that process:

| What | Where |
|---|---|
| Instructions the agent worked under | [`AGENTS.md`](AGENTS.md): the pipeline invariant, architecture rules, conventions |
| Skills written alongside the tool | [`skills/`](skills): `create-benchmark-task`, `investigate-regression`, `review-evaluation`. They are written for whoever operates agent-eval, human or agent. They live outside `.claude/skills/` so they don't load into sessions that build agent-eval; copy them there to use them with Claude Code. |
| One complete agent session | [`docs/transcripts/session-01.md`](docs/transcripts/session-01.md), rendered from the raw log [`session-01.jsonl`](docs/transcripts/session-01.jsonl), with the exact prompt and command |
| What was checked by hand, and where the agent was wrong | [`docs/agent-session-notes.md`](docs/agent-session-notes.md) |
| Agent-written bugs caught from the tool's own output | [`docs/trust-incident.md`](docs/trust-incident.md) |

The commit history keeps the agent's work and the human review apart. `9c2c202` is the
recorded session's output exactly as the agent left it. `d90f237` holds the fixes found in
review.

---

## Commands

| Command | What it does |
|---|---|
| `agent-eval doctor -b BENCH` | Validate a benchmark. Exits non-zero if any task is vacuous or unsolvable. |
| `agent-eval tasks -b BENCH` | List tasks, their checks and whether they have a reference. |
| `agent-eval evaluate -c CONFIG` | The main command: both arms, every task, N reps, then report. |
| `agent-eval judge -c CONFIG` | Re-judge stored patches without re-running any agent. |
| `agent-eval report -c CONFIG` | Rebuild the report from stored artifacts with new thresholds. |
| `agent-eval show RUN_ID -c CONFIG --diff` | Inspect one run — the bottom of the evidence chain. |
| `agent-eval index -o runs` | Rebuild the SQLite index from the files. |

Further reading: [`docs/architecture.md`](docs/architecture.md) ·
[`docs/decision-log.md`](docs/decision-log.md) ·
[`docs/manual-test.md`](docs/manual-test.md) ·
[`docs/trust-incident.md`](docs/trust-incident.md) ·
[`docs/interview-guide.md`](docs/interview-guide.md) · [`AGENTS.md`](AGENTS.md)

# Architecture — `agent-eval`

> An evaluation tool for coding-agent harnesses.
> Status: **design, pre-implementation.** Written before any implementation code.

---

## 1. Problem definition

A **harness** is everything around the model that shapes how a coding agent behaves:
`AGENTS.md` / `CLAUDE.md`, skills, rules, workflows, hooks, MCP servers, tool permissions,
and model/agent configuration. Teams change it weekly. Every change is made because
*someone believes it helps*. Nobody measures whether it did.

The person who owns the harness must decide: **roll this change out to 40 engineers, or not?**
Today that decision is made by reading three outputs and forming an opinion.

`agent-eval` replaces the opinion with **evidence**: run the same benchmark tasks under
harness A and harness B, with everything else held constant, repeat each task several times,
measure several independent dimensions of quality, and report a verdict *together with the
uncertainty around it*.

### Design constraints (self-imposed)

| Constraint | Consequence |
|---|---|
| A reviewer must be able to defend every number | No magic weighted score. No hidden normalisation. |
| Small effort budget (6–8h) | Sequential execution, one real agent adapter, no UI, no service. |
| Evidence must be inspectable | Raw stdout/diff/test-output on disk, linked from the report. |
| Honest uncertainty beats false precision | `INCONCLUSIVE` is a first-class, frequently-returned verdict. |
| A reviewer must not need an API key | Deterministic replay mode ships with recorded real runs. |

### Non-goals

Not a model leaderboard. Not SWE-bench. Not a CI gate (yet). Not a general agent framework.
It answers exactly one question: *did **this** harness change help **our** agent on **our** kind of work?*

---

## 2. Evaluation model

The spine of the system is a pipeline of **pure stages over an append-only artifact store**:

```
          plan                run                 check               judge
  config ──────► RunPlan ──────────► RunRecord ────────► CheckResults ───────► JudgeResults
                (what to run,       (raw agent I/O,     (objective,           (subjective,
                 in what order)      diff, usage)        deterministic)        blind, rubric)
                                            │                  │                    │
                                            └──────────┬───────┴────────────────────┘
                                                       ▼
                                             aggregate ──► TaskResult ──► ArmSummary
                                                       ▼
                                                    compare ──► Comparison (+ CIs)
                                                       ▼
                                                     decide ──► Verdict (+ reasons)
                                                       ▼
                                                     report ──► report.json + report.html
```

**Why stages, and why on-disk between every stage?**

1. **Re-runnability without re-spending money.** Agent execution costs minutes and dollars.
   Judging, aggregation, thresholds and the report are all cheap. If I change a threshold
   I re-run `decide` + `report` over stored artifacts — not 30 agent runs.
2. **Testability.** Every stage after `run` is a pure function `artifacts -> artifacts`.
   The entire test suite runs with zero network access.
3. **Auditability.** A reviewer who distrusts a number can walk backwards from the report
   cell to the exact `stdout.log` that produced it.

*What breaks without this?* A monolithic `evaluate()` that holds everything in memory:
one crash at run 27/30 loses the whole evaluation, thresholds can't be revisited, and
nothing below the top-level function is unit-testable without hitting an API.

---

## 3. Harness model

### Representation
A harness is a **directory of files** that gets overlaid onto the task workspace before the
agent starts:

```
harnesses/baseline/          harnesses/candidate/
  AGENTS.md                    AGENTS.md            (modified)
  .claude/settings.json        .claude/settings.json
                               .claude/skills/repo-conventions/SKILL.md   (added)
  harness.yaml                 harness.yaml         (model/agent config)
```

`harness.yaml` holds what is *not* a file in the repo: model id, agent adapter, permission
mode, MCP server list, extra CLI args. It is part of the hash, so "swapped the model"
and "added a skill" are both first-class harness changes.

### Identity: content hash, not git SHA

```
harness_id = sha256( for each file, sorted by relpath: relpath + "\0" + mode + "\0" + sha256(bytes) )
```
Rendered as `hns_3f8a91c2` (8 hex chars) in reports, full hash in `metadata.json`.

**Decision — content hash over git commit SHA.** A git SHA identifies a *repository state*,
not a *harness*; two different commits with identical harness files must compare as the
same harness, and an uncommitted local edit must produce a different id (a git SHA would
silently hide it). We *also* record the git SHA and dirty-flag when the harness lives in a
git repo, for traceability — but identity is content.

*What breaks without this?* You report "candidate abc123 is better", someone re-runs it,
gets different results, and you cannot tell whether the harness was actually identical.

### Application: overlay, with the overlay excluded from grading

Before each run: copy the harness files into the workspace root, then append every overlaid
path to `.git/info/exclude`, and record the overlaid file list on the run record.

**Why:** the harness files physically live inside the repo the agent is editing. Without
exclusion, `AGENTS.md` shows up in every diff, inflates "files changed", leaks the arm
identity to the blind judge, and pollutes lint runs. The alternative (out-of-tree config via
env vars/flags) is cleaner in theory but agent-specific and fragile; overlay is what the real
world looks like.

### Harness diff in the report
Computed from the two manifests: `ADDED / MODIFIED / REMOVED` per file, plus a
line-level unified diff for text files, plus an explicit `MODEL: unchanged` /
`MCP: unchanged` row from `harness.yaml`. Actionability requires the reader to see
*what changed* next to *what it did*.

---

## 4. Benchmark / task model

```
benchmarks/py-customers/
  benchmark.yaml            # name, fixture path, shared check definitions
  fixture/                  # the starting repository the agent edits
  tasks/
    t01-export-csv/
      task.yaml             # id, prompt, timeout, protected paths, checks, rubric ref
      rubric.md             # judged criteria, task-specific
      verify/               # tests copied in *after* the agent finishes
        test_t01.py
      reference/            # reference solution — used only by `doctor`, never by the agent
        src/customers/service.py
```

### `task.yaml` (shape)
```yaml
id: t01-export-csv
title: Add a CSV exporter for customers
prompt: |
  Add the ability to export customers to CSV ...      # natural language, as a human would write it
timeout_seconds: 600
protected_paths: ["tests/**", "pyproject.toml", "verify/**"]
checks:
  - {id: tests,  type: pytest,  args: ["-q", "verify/"]}
  - {id: lint,   type: command, cmd: ["ruff", "check", "."]}
  - {id: types,  type: command, cmd: ["mypy", "src/"]}
rubric: rubric.md
```

### Two properties every task must have

1. **Hidden verification.** The `verify/` tests are copied into the workspace *after* the
   agent stops. The agent never sees them. Otherwise we measure "can the agent read the
   grader", not "can the agent do the job".
2. **Golden check (`agent-eval doctor`).**
   - Verification must **FAIL** on the pristine fixture → proves the task is not vacuous.
   - Verification must **PASS** after overlaying `reference/` → proves the task is
     solvable and the checks are correct.
   - Objective quality checks (lint, types) must **PASS** on the pristine fixture →
     proves a lint failure later can be attributed to the agent rather than to the fixture.

   *Implementation note — `reference/` is a directory of whole files, not a
   `solution.patch`.* A patch file is brittle: any later edit to the fixture invalidates
   every stored patch and `doctor` starts failing for a reason that has nothing to do with
   the tasks. Overlaying whole files survives fixture edits, and the overlay machinery is
   the same code path the harness overlay already needs.

*What breaks without the golden check?* A task whose tests pass on an empty diff scores
1.0 for both arms and silently dilutes every delta toward zero. A task whose tests can never
pass scores 0.0 for both arms and does the same. Both are invisible in the final report.
This is the cheapest high-value integrity feature in the whole tool.

### The five tasks (deliberately different failure modes)

| # | Task | Property under test |
|---|---|---|
| t01 | Implement a missing feature | baseline capability |
| t02 | Fix a bug using the repo's existing error type | convention adherence (a harness rule should help here) |
| t03 | Add validation **and** tests | instruction-following on multi-part requirements |
| t04 | Change an API without breaking callers | regression awareness; hidden back-compat tests |
| t05 | Refactor preserving behaviour | not-breaking-things; near-zero functional signal, high judged signal |

t05 exists specifically to prove a point: **not every task produces a functional-correctness
signal.** A refactor task where tests pass in both arms is where the judge earns its keep.

---

## 5. Run model

**One run = one (task × harness × repetition).**

```
workspace:  fresh copy of fixture in a temp dir
            git init; git add -A; git commit -m base   -> base_commit
            overlay harness files; add them to .git/info/exclude
execute:    AgentRunner.run(workspace, prompt, harness, model, timeout)
capture:    exit code, stdout, stderr, wall-clock, usage (if exposed), turns (if exposed)
freeze:     git add -A; diff base..worktree            -> diff.patch, changed_files
restore:    git checkout base -- <protected_paths>     -> tamper flag if anything changed
verify:     copy verify/ in; run each check as a subprocess -> CheckResult[]
```

### `RunRecord` (persisted JSON)
```
run_id, evaluation_id, task_id, harness_id, arm(baseline|candidate), rep, order_index
model, agent_adapter, tool_version, prompt_sha256, started_at, ended_at, duration_s
exit_code, timed_out, base_commit, changed_files[], diff_stat{files,insertions,deletions}
usage{input_tokens, output_tokens, cache_*, source: actual|estimated|unavailable}
cost{usd, source: actual|estimated|unavailable}, turns
tamper{detected, paths[]}
env{os, python, cpu, agent_version}   # allow-listed keys only, never a full env dump
artifacts{stdout, stderr, diff, checks, judge}   # relative paths
```

### Run isolation
Each run gets a fresh copy of the fixture. Runs never share a workspace. Agent execution
happens with `cwd` inside the temp workspace; the tool never runs the agent against the
real repository.

### Execution order matters
Default `--order interleaved`: for each task, for each rep, run baseline then candidate
back-to-back. API latency, model routing and machine load drift over a 40-minute
evaluation; running all 15 baseline runs first and all 15 candidate runs after would
confound "harness" with "time of day". Order is recorded per run (`order_index`) and
`--order random --seed N` is available.

### Reward-hacking guard
`protected_paths` are restored from the base commit before verification, and any
modification is recorded as `tamper.detected`. The agent making tests pass by editing the
tests is a *real* observed failure mode; the tool must neither be fooled by it nor silently
discard it. Tamper is surfaced as a **MANUAL REVIEW** trigger, not an automatic fail.

---

## 6. Agent runner abstraction

```python
class AgentRunner(Protocol):
    name: str
    def run(self, req: AgentRunRequest) -> AgentRunResult: ...
```

| Adapter | Purpose |
|---|---|
| `claude-code` | `claude -p "<prompt>" --output-format json` in the workspace. Parses `total_cost_usd`, `usage`, `num_turns`, `session_id` when present. |
| `replay` | Replays a recorded cassette keyed by `(task_id, harness_id, rep)`. **Deterministic demo mode.** |
| `scripted` | Programmable fake used by the test suite (pass/fail/timeout/tamper on demand). |

**Record–replay, not fake data.** `claude-code` writes a cassette for every run. Those
cassettes (real diffs, real durations, real token counts from a real evaluation) are
committed to the repo, so `agent-eval evaluate --config examples/demo.yaml` reproduces a
**real** evaluation with no API key and no cost. A random fake would make the demo report
meaningless; a replay makes it honest.

*Known constraint:* usage/cost fields depend on the CLI exposing them. If they are absent,
the field is written as `unavailable` — never invented, never back-filled with a guess.
The report renders `actual` / `estimated` / `unavailable` distinctly.

---

## 7. Metric model

Five dimensions, deliberately **not** collapsed into one number.

| # | Dimension | Type | Source | Unit of measure |
|---|---|---|---|---|
| 1 | Functional correctness | objective | task `tests` check exit code | pass ∈ {0,1} per run → pass-rate per task |
| 2 | Requirement adherence | **judged** | LLM judge vs rubric, blind | 1–5 per criterion |
| 3 | Convention / quality | mixed | `lint`, `types` exit codes (objective) + judge criterion (subjective), reported separately | 0/1 and 1–5 |
| 4 | Efficiency | objective | wall-clock, tokens, cost, turns | median per task |
| 5 | Reliability | objective | agreement across repetitions | consistency ∈ [0,1] |

### Objective vs judged — a hard wall
The report renders these in separate blocks with different visual treatment and an explicit
legend. An LLM score is never displayed as a fact. Every judged number carries the model id,
prompt version and a link to the raw response.

### Aggregation path (and why)
`run → task → arm`, never `run → arm` directly.

Runs within a task are **not independent** — they share a fixture, a prompt and a difficulty.
Pooling 15 runs as 15 samples overstates the sample size ~3×, which produces confidence
intervals that are too narrow, which produces confident wrong verdicts. So:
per-task pass-rate first (3 reps → p ∈ {0, ⅓, ⅔, 1}), then arm-level statistics **over the 5
tasks**. The effective sample size is **5, not 15**. The tool says so, out loud, in the report.

---

## 8. LLM judge model

```python
class LLMProvider(Protocol):
    def complete(self, system: str, user: str, *, model: str, max_tokens: int,
                 temperature: float) -> ProviderResponse: ...

class Judge:
    def evaluate(self, task, patch, repo_context, rubric) -> JudgeResult
```
Providers: `AnthropicProvider`, `MockProvider` (deterministic, used by tests).

### What the judge sees — and what it must not see

| Sees | Does **not** see |
|---|---|
| task prompt | which arm produced the patch |
| task rubric | harness contents or name |
| the unified diff (harness files excluded) | **test results** |
| repo conventions file + pre-images of touched files | lint/type output |
| | the other arm's patch |

Three biases avoided by construction:
- **Positional bias** — absolute scoring against a rubric, never pairwise A-vs-B.
- **Arm/prestige bias** — the judge is blind to the arm label; patches are presented
  under a neutral id.
- **Outcome contamination** — see §9.

### Output contract (schema-validated; invalid JSON = retry once, then `judge_error`)
```json
{"criteria": {"requirement_completeness": {"score": 4, "evidence": ["src/export.py:12 adds ..."]},
              "convention_adherence":     {"score": 3, "evidence": ["..."]},
              "maintainability":          {"score": 4, "evidence": ["..."]}},
 "confidence": 0.72, "reasoning": "..."}
```
Evidence strings **must** cite file:line from the patch. An evidence-free criterion is
downgraded to `unscored` — this is the cheapest defence against a fluent, ungrounded judge.

### Judge reliability is measured, not assumed
- `judge.self_consistency: 2` → each patch is judged twice, independently. If any criterion
  differs by ≥2 points, the result is flagged `low_agreement` and excluded from the
  aggregate delta (still shown in the report).
- The judge prompt is versioned (`requirement_adherence.v1.md`) and its version + model id
  are stored on every result. Changing the prompt changes the version; results from
  different prompt versions are never pooled.
- `agent-eval judge --rerun` re-judges stored patches without re-running agents.

### Privacy
Judging sends your patch and parts of your repository to a third-party model. Judging is
therefore **opt-in** (`--judge` / `judge.enabled`), off for the offline demo, and documented
in the README. Redaction runs before anything leaves the machine.

---

## 9. Avoiding double counting

The failure mode: tests pass → judge reads "tests pass" → judge scores 5 → the report shows
two independent-looking green signals that are really *one* signal counted twice, and the
verdict inherits false confidence.

Four concrete defences:

1. **The judge never receives test output.** Structural, not a prompt instruction.
2. **The rubric forbids outcome-based reasoning.** "Do not speculate about whether tests
   pass. Score only what the patch itself shows."
3. **No metric is summed into another.** Correctness and adherence enter the decision rule as
   *separate gates*, never as terms of a weighted sum (§11).
4. **Disagreement is surfaced, not smoothed.** The report contains a 2×2:

   |                | judge ≥ 4 | judge ≤ 3 |
   |---|---|---|
   | **tests pass** | agreement — high confidence | ⚠ *passes tests, poor requirement fit* → MANUAL REVIEW |
   | **tests fail** | ⚠ *looks right, fails tests* → check the task, not the agent | agreement — high confidence |

   The off-diagonal cells are the most valuable output of the entire tool. They are where
   either the agent, the benchmark, or the judge is wrong — and a human must look.

Objective quality checks (lint/types) and the judged convention score cover overlapping
ground by design; they are reported as separate rows and only the **objective** one is a
guardrail in the decision rule.

---

## 10. Comparison and statistics

Deliberately simple, deliberately honest. No p-values theatre.

### Unit of analysis: the task (cluster), n = 5
### Primary estimand: paired per-task difference

```
d_i = pass_rate(candidate, task_i) − pass_rate(baseline, task_i)      i = 1..5
Δ   = mean(d_i)
```
**Paired, because tasks differ enormously in difficulty.** Comparing pooled means throws
away the pairing and inflates variance with between-task noise that cancels exactly in a
paired design. The tasks are identical across arms — use that.

### Uncertainty: cluster bootstrap over tasks
Resample the 5 tasks with replacement, 10 000 draws, recompute Δ → percentile 95% CI.
Chosen over a t-test because n=5 with a bounded, discrete outcome badly violates normality,
and over exact tests because the clustering is the dominant issue. The bootstrap is ~12 lines,
explainable in one sentence, and does not pretend to more precision than it has.

Also reported (descriptive, not decisive): per-arm pooled pass rate with a **Wilson**
interval (correct at small n and near 0/1 where the normal approximation is nonsense).

### Continuous metrics (duration, tokens, cost)
Per task: **median** across repetitions (agent durations are long-tailed; one retry storm
should not move the number). Then paired median difference across tasks + bootstrap CI.
p90 duration reported as a tail/guardrail metric.

### Reliability
```
consistency(arm, task) = (# reps matching the modal outcome) / reps
flaky_tasks(arm)       = # tasks with 0 < pass_rate < 1
```

### Stated power, up front
With 5 tasks × 3 reps the tool can only resolve **large** paired effects (roughly ≥ 30
percentage points). This sentence is printed in the report, not buried in the README.
`9/10 vs 8/10` is noise and the tool will say so.

---

## 11. Decision model

**No weighted composite score.** Instead: one pre-declared **primary metric** plus
**guardrails** — the standard structure of a real product experiment — evaluated as ordered gates.

```
GATE 0  COMPARABILITY   model / task set / fixture / tool version identical across arms?
                        → else VERDICT = NOT_COMPARABLE (refuse to answer)

GATE 1  BLOCKER         ∃ task: baseline modal = PASS and candidate modal = FAIL
                        → VERDICT = NEGATIVE ("critical regression on t0X")

GATE 2  INTEGRITY       tamper detected, judge low_agreement above threshold,
                        or test/judge disagreement (§9 off-diagonal)
                        → attach MANUAL_REVIEW_REQUIRED (annotates; never silently flips)

GATE 3  PRIMARY         paired correctness Δ, 95% bootstrap CI
                        CI_low  > +min_effect → improved
                        CI_high < −min_effect → regressed
                        else                  → no detectable difference

GATE 4  GUARDRAILS      cost/task ↑ > tolerance | p90 duration ↑ > tolerance |
                        objective quality (lint/types) ↓ | reliability ↓ > tolerance |
                        judged adherence ↓ > tolerance   → guardrail breach (each listed)

VERDICT
  POSITIVE      primary improved, no blocker, no guardrail breach
  POSITIVE      primary flat AND cost improved beyond tolerance AND no breach   ("efficiency gain at equal correctness")
  NEGATIVE      blocker, or primary regressed, or breach without primary improvement
  INCONCLUSIVE  everything else — including "candidate looks better but the CI contains zero"
```

All thresholds live in `config.decision` with documented defaults
(`min_effect: 0.20`, `cost_tolerance: 0.15`, `duration_tolerance: 0.25`,
`reliability_tolerance: 0.15`). The report prints the thresholds it used.

**Why gates instead of weights.** A weighted score requires a defensible exchange rate
between "one more passing task" and "12¢ per run". No such rate exists; inventing one hides
the value judgement inside a constant and makes the verdict unarguable. Gates put the value
judgement in the open, as named, configurable, per-dimension tolerances — and let a
regression on one critical task veto an average improvement, which a weighted sum can never do.

The verdict is **always** accompanied by a `reasons[]` list naming the gate that fired.

---

## 12. Report model

Two outputs from one `ReportModel`:
- `report.json` — machine-readable, stable schema, every number the HTML shows.
- `report.html` — single self-contained file (inlined CSS, no CDN, no JS build). The reviewer experience.

Sections, in order: overview → comparability & harness diff → **executive verdict + reasons**
→ functional correctness → requirement adherence (judged) → quality/conventions
→ efficiency & cost → reliability → per-task table → regressions → improvements
→ test/judge disagreement matrix → manual-review queue → limitations → raw artifact index.

Every metric cell links to the relative path of the artifact that produced it.

---

## 13. Storage model

**Files are the source of truth. SQLite is a rebuildable index.**

```
runs/<evaluation-id>/
  metadata.json          config snapshot, harness manifests+hashes, env, tool version, seed
  plan.jsonl             every planned run, in execution order
  runs/<task>/<arm>/rep-01/
      run.json  stdout.log  stderr.log  diff.patch  checks.json  judge.json  cassette.json
  summary.json           aggregated metrics + stats
  report.json  report.html
index.sqlite             derived; `agent-eval index --rebuild` regenerates it from runs/
```

**Why not SQLite as the primary store?** A reviewer can `cat` a file; they cannot `cat` a
database. Debugging an evaluation means reading a diff and a stderr log. SQLite earns its
place only for cross-evaluation queries ("has t04 ever passed under any harness?") — so it
exists, but as a cache that can be deleted without losing anything.

---

## 14. Reproducibility

Recorded on every evaluation: tool version + git SHA, harness manifests + hashes, benchmark
task-set hash, fixture hash, model id and any version string the adapter reports, agent
adapter version, prompt SHA-256, seed, execution order, per-run timestamps, OS/python/CPU,
and the exact check commands.

Agents are **not deterministic** even at temperature 0 (tool ordering, cache state, model
routing). The tool therefore does not promise identical results on re-run; it promises that
(a) everything that *can* be pinned is pinned and recorded, (b) the variance that remains is
measured and reported rather than hidden, and (c) every *post-run* stage — checks, judging,
aggregation, decision, report — is fully deterministic and replayable from artifacts.

---

### Implementation note: tool caches must be excluded from git at workspace creation

The checks themselves write into the workspace — `pytest` creates `src/**/__pycache__`,
`mypy` creates `.mypy_cache/`. The first `doctor` run failed because of this: the t05
"did the agent actually change anything?" check saw the `.pyc` files pytest had just
written and reported that the untouched fixture *had* changed. The same bug would have
put `.pyc` files in every agent diff, inflated `changed_files`, and shown them to the
judge. Workspace creation now writes those patterns into `.git/info/exclude` before the
base commit. Covered by `tests/test_workspace.py::test_tool_caches_never_appear_in_the_diff`.

## 15. Limitations (stated before a single number is produced)

1. **n = 5 tasks.** Only large effects are detectable. Most real harness changes will honestly
   read `INCONCLUSIVE` at this sample size.
2. **Benchmark validity.** A harness tuned against these 5 tasks will score well on these 5
   tasks. Tasks must be rotated and partly held out; the tool cannot detect its own overfitting.
3. **Judge reliability.** Measured only by self-consistency, not against human labels.
   A consistent judge can be consistently wrong.
4. **Single agent adapter.** Claude Code only. The interface supports more; the evidence does not.
5. **Single machine, sequential.** No isolation from host state beyond a temp workspace.
6. **Cost fidelity** depends entirely on what the adapter exposes; `unavailable` is common and honest.
7. **Checks run in the evaluator's own Python environment.** A task cannot yet declare
   its own dependencies; `pytest`, `ruff` and `mypy` come from the environment agent-eval
   is installed into. Fine for one fixture, wrong for a benchmark spanning several
   projects. `environment requirements` is reserved on the task model but unimplemented.
8. **The fixture is small.** Conclusions may not transfer to a 500k-line monorepo, which is
   exactly where harness quality matters most.

---
---

# Part II — as built

The design above was written before implementation. This section records where the build
diverged from it, and why. Keeping both is deliberate: the gap between a design and its
implementation is usually where the interesting decisions are.

## Divergences from the design

### 1. Reference solutions are directories, not `solution.patch`
**Design:** each task stores `solution.patch`, applied by `git apply` during `doctor`.
**Built:** each task stores `reference/`, whole files copied over the fixture.
**Why:** a patch file is invalidated by any later edit to the fixture, so `doctor` starts
failing for reasons unrelated to the tasks and you spend the afternoon regenerating patches.
Whole-file overlay survives fixture edits and reuses the overlay code path the harness
already needs — one mechanism instead of two. Cost: a `reference/` file can drift from the
fixture, mitigated because `doctor` runs lint and mypy over the reference too.

### 2. Non-behavioural config keys are excluded from the harness hash
**Design:** the hash covers every file byte-for-byte.
**Built:** `harness.yaml` is hashed by its *behavioural* content — `name` and `description`
are excluded, and the remaining keys are hashed as canonical JSON.
**Why:** a test caught the inconsistency. Renaming a harness changed its identity while
`diff_harnesses` correctly reported that nothing behavioural had changed. Identity and diff
must agree, and a rename must not invalidate every recorded cassette. Key reordering in the
YAML is likewise not a change.

### 3. Protected-path restore uses `--diff-filter=MD`
**Design:** "restore protected paths from the base commit".
**Built:** only files that existed at base and were **M**odified or **D**eleted are reverted;
**A**dded files are left alone.
**Why:** t03 explicitly asks the agent to add tests, and blanket protection would delete the
work the task requested. The distinction is the right one conceptually: we are not forbidding
new tests, we are preventing the agent from weakening verification that was already there.
Caught by `test_restore_keeps_newly_added_tests`.

### 4. Tool caches are excluded from git at workspace creation
Not in the design at all; found by the first `doctor` run. The checks write into the
workspace — pytest creates `src/**/__pycache__`, mypy creates `.mypy_cache/` — so a check
running after them saw the untouched fixture as modified. Without the fix, every agent diff
would have contained `.pyc` files and the judge would have been shown compiled bytecode.
See `docs/trust-incident.md` §1.

### 5. A third runner: `simulated`
**Design:** `claude-code`, `replay`, and a `scripted` fake for tests.
**Built:** those three plus `simulated`, which picks a canned attempt per (task, arm, rep) by
seeded draw and copies real Python into the workspace.
**Why:** `replay` only works once real runs exist, and the brief requires a reviewer to see a
complete evaluation with no API key. `simulated` fills that gap with *real* patches that
*really* run through pytest/ruff/mypy — only the attempt selection and the duration/token
figures are declared. `AgentUsage.source` gained a fourth value, `"simulated"`, so a demo
number can never be mistaken for one a provider reported.

### 6. `UsageSource` has four values, not three
`actual | estimated | simulated | unavailable`. `simulated` is first-class rather than a
flavour of `estimated`, because the report must be able to say which numbers came from a
provider and which from a YAML file.

### 7. The judge gained a `heuristic` provider
Rule-based, not a model, reporting itself as `heuristic-demo-1 (NOT an LLM)`. It exists so the
offline demo can exercise the judged dimension and the disagreement matrix — the parts of the
design that matter most — without an API key. Its rules are stated in its docstring.

### 8. The judge discards unevidenced criteria rather than scoring them
**Design:** "evidence strings must cite file:line".
**Built:** a criterion whose evidence contains no `file.ext:line` match has its score replaced
with `None` and a `discarded_reason`, and never reaches the aggregate.
**Why:** stating a requirement in a prompt is not enforcing it. An unevidenced score from a
fluent model is indistinguishable from a confident guess, and the cheapest defence is to
refuse to count it.

### 9. The decision model gained judge thresholds — and they are shared
`judge_high_threshold` (4.0) and `judge_low_threshold` (3.0) live in `DecisionSettings` and
are used by **both** the manual-review flags and the report's matrix. The first version had
different hardcoded numbers in each place and produced a report that contradicted itself.
See `docs/trust-incident.md` §2.

### 10. The disagreement matrix is 2×3, not 2×2
A middle band was added between "high" and "low". Without it, every score below the high
threshold landed in the alarming cell, so a judge with a central tendency of 3.3 flagged
almost everything. Same root cause as #9.

### 11. `resolvable_effect()` replaced a closed-form MDE
The design said "state the minimum detectable effect". The first implementation used a
closed-form approximation and returned 0.60 for a design the bootstrap comfortably resolved
at 0.27 — a number contradicting the tool's own analysis. The replacement asks how many tasks
must improve by one repetition before the bootstrap interval clears zero, **using the same
estimator the report uses**. It also independently justifies the `min_effect: 0.20` default,
which had been chosen by feel.

### 12. The report has 10 sections, not 15
Merged: harness comparison into the overview; the metric dimensions into one table tagged
`measured`/`judged`; regressions and improvements into one section; raw run references into
the per-run table. The design's section list was a checklist of content, not of headings, and
15 headings for this much data reads as padding.

### 13. CLI commands
**Design:** `init`, `run`, `evaluate`, `report`, `compare`.
**Built:** `doctor`, `tasks`, `evaluate`, `judge`, `report`, `show`, `index`.
`init` was cut — scaffolding a benchmark is four `mkdir`s documented in the README, and a
generator would be one more thing to maintain. `run` (a single arm) and `compare` (two stored
evaluations) were cut as unused: `evaluate --task` covers the first, and nothing in the
workflow needed the second. `doctor` and `show` earned their place — one guards every
evaluation, the other is the bottom of the evidence chain.

## What the final system looks like

```
28 source files · 149 tests, all offline · ruff and mypy clean
5 benchmark tasks, each with hidden verification, a rubric and a reference solution
11 canned attempts for the offline demo
30-run demo evaluation in ~35 seconds with no API key and no spend
```

## The parts I would attack first, in a review

1. **Benchmark validity.** The convention-sensitive tasks (t02, t03) are the ones that move
   the result. They are designed so the convention is inferable from the fixture without the
   candidate harness — but that is an argument, not a measurement, until a real baseline run
   shows the baseline arm can sometimes get them right.
2. **Judge thresholds on an uncalibrated scale.** Documented, configurable, still arbitrary.
3. **n = 5.** Every conclusion this tool can reach is bounded by it.
4. **One adapter.** The `AgentRunner` interface is three methods and has three
   implementations, but only one of them talks to a real agent.

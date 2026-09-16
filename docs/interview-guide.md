# Interview guide

Concise, defensible answers. Where a claim is checkable, the file is named.

---

### 1. Why Python?
The checks are subprocesses and the analysis is arithmetic, so the language buys nothing
exotic. Python wins on two specifics: the fixture is Python, so `pytest`/`ruff`/`mypy` give
three deterministic signal channels with zero setup; and the statistics are twelve lines of
standard library rather than a dependency. The runner is language-agnostic — a check is an
argv and an exit code.

### 2. Why SQLite?
As a **rebuildable index**, not a store. Files are the source of truth: a reviewer can `cat`
a diff and a stderr log, and debugging an evaluation is exactly that. SQLite earns its place
only for cross-evaluation queries ("has t04 ever passed under any harness?"). `agent-eval
index` regenerates it; deleting it loses nothing, and a test asserts that.

### 3. Why not a web UI?
The output is one self-contained HTML file that opens offline forever and can be attached to
a PR. A UI needs a server, and a server makes the artifacts stop being the interface. The
reviewer experience I want is "open the file, then `cat` the evidence it links to".

### 4. Why not one overall score?
Because a composite needs an exchange rate between "one more task passes" and "twelve cents
a run", and no such rate exists. Inventing one hides a value judgement inside a constant and
makes the verdict unarguable — a reader who disagrees has nowhere to put the objection.
Gates make the judgements explicit, named and configurable. And gates let one critical
regression veto an average improvement, which a weighted sum mathematically cannot: four
tasks always outvote the fifth. (`decide.py`)

### 5. How do you know the LLM judge is reliable?
I don't, and the tool says so. What it *does* is measure the judge's **precision**: every
patch is judged twice independently, and a criterion whose scores differ by ≥2 on a 5-point
scale is flagged `low_agreement`, excluded from the aggregate, and sent to manual review. So
a noisy judge widens the review queue instead of quietly moving the verdict. Precision is
not accuracy — validating against human labels is the calibration work I cut, and it is
limitation #3 in the README.

### 6. How do you handle evaluator bias?
Three, each designed out structurally rather than prompted away.
**Positional** — absolute rubric scoring, never pairwise A-vs-B, so there is no position.
**Arm/prestige** — the judge is never told which arm it sees, and harness files are excluded
from the patch via `.git/info/exclude`.
**Outcome contamination** — test results are not in the prompt, and `Judge.evaluate` has no
parameter that could carry one; a test asserts the exact parameter set.
Plus: evidence must cite `file:line`, and a criterion without grounded evidence is discarded
rather than scored.

### 7. Why multiple runs?
Agents are non-deterministic even at temperature 0. One run per task cannot distinguish
"works" from "worked once". Concretely, from `stats.resolvable_effect()`: 5 tasks × 1 rep
resolves a 60 percentage-point difference; 5 × 3 resolves 20pp. That is the argument, and
it's a test (`test_more_repetitions_resolve_smaller_effects`).

### 8. How many runs are enough?
Depends on the effect you need to see, and the tool computes it with the same estimator the
report uses:

| design | smallest resolvable paired difference |
|---|---|
| 5 × 1 | 60 pp |
| 5 × 3 | 20 pp |
| 10 × 3 | 13 pp |
| 20 × 5 | 4 pp |

Adding **tasks** beats adding repetitions, because the unit of analysis is the task.
Repetitions buy resolution *within* a task; tasks buy sample size.

### 9. How do you handle flaky benchmarks?
Three ways. `doctor` refuses vacuous and unsolvable tasks before anything runs. Flakiness is
*measured* — per-task consistency, and `flaky_tasks` per arm. And a task that becomes flaky
under the candidate raises a reliability regression, because "it still passes, just not
always" is a real degradation that a mean hides.

### 10. What if the candidate is slower but more correct?
The tool refuses to make that trade for you, and says so: verdict `INCONCLUSIVE`, headline
*"improved correctness but breached a guardrail — whether that trade is worth making is a
judgement this tool will not make for you."* Both numbers are on the page with their
intervals. Pretending a constant resolves that is exactly the failure mode in Q4.

### 11. What if tests pass but the code quality is poor?
That is the point of separating the channels, and it has a dedicated output: the
measured-vs-judged matrix. A task in the "passes tests / judged low" cell goes to the manual
review queue with the artifact path attached. The verdict is annotated, never silently
flipped — automation flags it, a human decides.

### 12. What if baseline and candidate use different models?
Gate 0 returns **NOT_COMPARABLE** and the tool refuses to produce a verdict. Attributing a
model upgrade to a new `AGENTS.md` is the single most expensive mistake this tool exists to
prevent. There's a test for it.

### 13. How do you isolate harness changes from model changes?
The model lives in `harness.yaml` and is part of the harness content hash, so a model swap
*is* a harness change and is visible in the report's harness diff. To evaluate both, run the
2×2: `A+X vs B+X`, then `A+Y vs B+Y`. Everything else — fixture, task set, seed, tool version,
execution environment — is recorded in `metadata.json` and rendered in the comparability
section.

### 14. How do you handle non-deterministic agents?
Three ways. Pin what can be pinned (seed, order, fixture hash, harness hash, prompt hash,
tool version) and record it. Measure what cannot be pinned — that is what repetitions and
the reliability dimension are for. And make everything *after* the agent fully
deterministic: checks, judging, aggregation, decision and report are pure functions over
artifacts and replay identically.

### 15. How would this scale?
The design already separates the expensive stage from the cheap ones, so scaling is mostly
parallelism: `execute_run` is independent per run and each run already gets its own temp
workspace, so a process pool over the plan is a small change. Beyond that: per-task
environments (containers), a shared artifact store rather than a local directory, and
sampling tasks rather than running all of them on every harness change. What would *not*
change is the analysis — the unit stays the task and the estimator stays paired.

### 16. What would you build next with another week?
In order:
1. **Judge calibration** — score a labelled set of known-good/known-bad patches, set the
   thresholds from the observed distribution, and report judge accuracy alongside its scores.
   This is limitations #3 and #4, and the direct cause of the trust incident.
2. **More tasks, and a held-out set** — n=5 is the binding constraint on every conclusion.
3. **Parallel execution + resume** — turns a 40-minute evaluation into a coffee break.
4. **Per-task environments** so a benchmark can span languages.
5. **CI mode** — run on every harness PR, fail on a blocker, comment the verdict.

### 17. What did you intentionally cut?
Parallelism, resume, extra agent adapters, Docker sandboxing, per-task environments, pairwise
judging, judge calibration, cost estimation where the provider reports nothing, trend
tracking, web UI, database server, auth, CI. All scope against 6–8 hours. The one that hurts
is judge calibration — see Q18.

### 18. What result did you not trust?
The demo's disagreement matrix flagged **four of five tasks** as "passes tests but judged
poor", while the manual-review queue — same data, same report — listed **two**. An
implausible rate is suspicious; a report contradicting itself is disqualifying.

Cause: one concept expressed twice. `metrics.py` flagged at `score <= 3.0`; the matrix called
anything below `4.0` "low"; nothing sat between. Underneath was a worse assumption — that a
judge's absolute scale means something. The demo judge centres on 3.3, so against a `>= 4.0`
bar nothing could ever be "high": the matrix was measuring the judge's central tendency and
reporting it as evidence about the agent.

Fixed: one source of truth in `decision:` config used by both, a middle band in the matrix,
and a stated caveat next to the table. Still open: the thresholds are absolute numbers on an
uncalibrated scale. I moved the knob into the open and documented it; I did not calibrate it.
(`docs/trust-incident.md`)

### 19. How would you evaluate an MCP change?
MCP servers are declared in `harness.yaml`, so they are already part of the harness hash and
appear in the harness diff. What is missing is **task design**: a benchmark whose tasks
cannot benefit from the server measures nothing. You need tasks that require the capability
the server provides, plus tasks that do not, so you can see whether it helps where it should
and costs where it shouldn't. I'd also add tool-call counts to the run record, because an
MCP's main risk is context and latency cost rather than correctness.

### 20. How would you evaluate a new skill?
Same machinery, but be deliberate about the tasks. A skill is targeted, so the benchmark
needs tasks the skill is meant to help with **and** tasks it is not — otherwise you measure
the skill on home turf and learn nothing about regression. Watch the cost guardrail
especially: skills add context to every run, including the ones that never use them. That is
precisely the shape the shipped demo shows — correctness up, cost up 46%, verdict NEGATIVE.

### 21. How would you evaluate a model change?
Identical mechanics — the model is in `harness.yaml` and part of the hash — but the framing
changes: gate 0 exists to stop you conflating a model change with a harness change, so
evaluate one at a time. A model change also deserves a wider benchmark than a prompt change,
because its effects are broader and less predictable, and cost is likely to be the dominant
guardrail rather than a side note.

### 22. How would you prevent benchmark overfitting?
Honestly: this tool cannot detect its own overfitting, and that is limitation #2. What
helps — a held-out task set the harness authors never see; rotating tasks on a schedule;
tracking whether benchmark improvements show up in production signals; and treating a
harness change that only helps one task with suspicion. The deeper protection is that tasks
are drawn from real work rather than written to discriminate between harnesses.

---

## Likely follow-ups

**"Show me where the judge could see a test result."** It cannot:
`inspect.signature(Judge.evaluate)` has exactly `task_prompt, patch, repo_context, rubric,
task_id, run_id`, and a test asserts that set. The data sections of the rendered prompt are
separately scanned for outcome words.

**"Your confidence interval is wide — isn't that useless?"** It *is* the finding. With five
tasks the interval should be wide, and a tool that reported a narrow one would be lying. The
actionable output is "this experiment cannot resolve what you're asking; here is the design
that could" — which the report states as a number.

**"Why is the demo NEGATIVE — isn't the candidate better?"** The point estimate is +13.3pp
and two tasks improved. But the correctness interval contains zero while the cost increase
does not: harm is demonstrated, benefit is not. The headline says exactly that, including
"correctness moved +13.3pp, but the interval contains zero, so that movement is not evidence
of anything" — so nobody reads NEGATIVE as "correctness fell".

**"What's the weakest part of this?"** Benchmark validity. Five small tasks in one clean
Python repository, and the convention-sensitive tasks are the ones that move the result. If
the hidden tests turned out to assert something only the candidate `AGENTS.md` states, the
whole evaluation would be theatre. I designed against it — every convention is inferable
from the fixture itself — but only a real run with a real baseline proves it.

**"Why is `doctor` a separate command rather than always-on?"** It is always-on: `evaluate`
runs it before spending anything and refuses to proceed on a broken benchmark. It is exposed
separately because benchmark authors need to run it while iterating, and because it is the
fastest way to show a reviewer that the integrity layer is real.

**"What did the tool catch that you would have missed?"** Two things, both written up in
`docs/trust-incident.md`. `doctor` reported an untouched fixture had changed — the cause was
that pytest wrote `__pycache__` into the workspace before the "did anything change?" check
ran, which would have put bytecode in every agent diff and shown it to the judge. And the
report contradicting itself about the judge thresholds, which is Q18.

---

## Demo script for the live session

Five minutes, and it lands the three ideas that matter.

```bash
agent-eval doctor -b benchmarks/py-customers        # "the benchmark is validated first"
```
Then break a task on purpose (§5 of `docs/manual-test.md`) and re-run it — `BROKEN / VACUOUS`,
exit 1. **Idea 1: a task that cannot distinguish two harnesses is invisible in the report,
so it is checked before anything is spent.**

```bash
agent-eval evaluate -c examples/demo.yaml           # 30 runs, ~35s
```
Open the report, go to §3. **Idea 2: measured and judged signals are separated and never
summed, and the interval is what decides — not the point estimate.**

```bash
sed -i '' 's/min_effect: 0.20/min_effect: 0.05/' /tmp/loose.yaml
agent-eval report -c /tmp/loose.yaml                # milliseconds, no agent runs
```
**Idea 3: every stage after the agent is a pure function over stored artifacts, so the
thresholds are arguable without re-spending anything.** Then `agent-eval show <run-id>
--diff` to walk from the verdict down to a patch.

If they change the problem — a new metric, a new dimension, a different verdict rule — the
answer is almost always "add a stage that reads existing artifacts", and you can demonstrate
it without re-running a single agent.

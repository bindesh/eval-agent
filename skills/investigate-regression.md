---
name: investigate-regression
description: >
  Use when an agent-eval report shows a regression, a manual-review flag, or a result that
  looks too good — to find out whether the agent, the benchmark, or the tool is at fault.
---

# Investigating a regression

The report tells you *that* something changed. This is how to find out *what*, and whether
to believe it.

## 0. Suspect the benchmark and the tool before the agent

An evaluation tool that reports a surprising finding is usually reporting a bug in itself.
Work in this order: **tool → benchmark → harness → agent**. Two of the three problems found
while building this one were in the tool.

## 1. Reproduce from artifacts — do not re-run the agent

```bash
agent-eval report -c examples/demo.yaml          # rebuild the verdict from stored runs
agent-eval show <run-id> -c examples/demo.yaml --diff
```

Nothing below needs another agent run. If you find yourself re-running agents to
investigate, stop — the artifacts have everything.

## 2. Read the actual patch

```bash
cat runs/<eval-id>/runs/<task>/<arm>/rep-01/diff.patch
```

Ask: did the agent do something reasonable that the *tests* reject? That is a benchmark
bug, not an agent regression. It is the single most common finding.

## 3. Read the check output, not the summary

```bash
jq -r '.[] | select(.passed==false) | .stdout' runs/<eval-id>/runs/<task>/<arm>/rep-*/checks.json
```

Distinguish: a real assertion failure · an import or collection error (benchmark bug) · a
timeout (raise `timeout_seconds`) · a lint failure that has nothing to do with the task.

## 4. Check whether it is just noise

Look at the per-task row. `2/3 → 1/3` is one repetition. At five tasks and three reps this
tool resolves about 20 percentage points; a single flip is inside the noise. The report's
own confidence interval already says this — check whether it contains zero before
investigating further.

## 5. Check whether protected files were touched

```bash
jq '.tamper' runs/<eval-id>/runs/<task>/<arm>/rep-*/run.json
```

`detected: true` means the agent modified the existing test suite. The files were restored
before verification, so the score is honest — but the *behaviour* is a finding in itself.

## 6. If the judge and the tests disagree

Open `judge.json` and read the `evidence` arrays, not the scores. Then:

- evidence cites real lines and the reasoning holds → the patch probably games the tests;
- evidence is vague or cites nothing → the judge is guessing; check `low_agreement`;
- `disagreement` shows a spread ≥ 2 → the judge disagrees with *itself*, and its score on
  this task was already excluded from the aggregate.

Also check the thresholds: `judge_high_threshold` / `judge_low_threshold` are absolute
numbers on an uncalibrated scale. A judge whose scores cluster at 3 will flag everything.

## 7. If the harness is implicated

```bash
jq '.harness_diff' runs/<eval-id>/report.json
```

Confirm `MODEL: unchanged`. If the model moved with the harness, the result is
uninterpretable and the tool should have returned `NOT_COMPARABLE` — if it did not, that is
a bug in gate 0.

## 8. Write down what you concluded

If the finding was a tool or benchmark bug: fix it, add a regression test named after the
symptom, and re-run `agent-eval doctor`. If it was real: say so in the report's terms —
which task, how many repetitions, and whether the interval clears zero.

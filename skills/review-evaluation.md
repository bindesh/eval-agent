---
name: review-evaluation
description: >
  Use when reading an agent-eval report before acting on it, to check whether the verdict
  is supported by the evidence underneath it.
---

# Reviewing an evaluation before you trust it

A verdict is a claim. This is the checklist for deciding whether to act on it.

## 1. Is the comparison controlled?

Section 2 of the report. **`MODEL: unchanged`** must be true. If the model moved with the
harness, nothing below is interpretable and the verdict should read `NOT_COMPARABLE`.

Also check: same benchmark, same task set, same tool version, `git_dirty: false` on both
harnesses. A dirty harness means the recorded id does not describe what actually ran.

## 2. Is it simulated?

If the report carries the dashed `SIMULATED AGENT` banner, the durations, token counts and
costs were declared by the benchmark, not measured. The patches and checks are real. Do not
cite the numbers as evidence about a real agent.

## 3. Could this experiment have seen the effect?

Section 3, "Resolving power". Five tasks × three reps resolves about 20 percentage points.
If the claimed improvement is smaller than that, the tool is reporting noise no matter how
positive the point estimate looks.

Then: **does the confidence interval contain zero?** If it does, the honest reading is "no
detectable difference", whatever the headline number.

## 4. Is the effective sample what you think?

`n` is the number of **tasks**, not runs. Then look at the per-task table and subtract the
tasks that were 3/3 in both arms or 0/3 in both — those contribute nothing to a paired
difference. Five tasks with two ceilings and one floor is really n=2.

## 5. Read the per-task table before the headline

One regressed task can matter more than a better average. Ask whether the regressed task is
one you care about — the gates treat all tasks equally, and your codebase does not.

## 6. Work the manual-review queue

Every entry needs a human. Specifically:

- **tamper** — read the diff. The agent tried to change the verification.
- **passes tests but judged poor** — read the patch. Either it satisfies the letter of the
  tests without the requirement, or the judge is wrong. Both are worth knowing.
- **judged good but fails tests** — suspect the benchmark first.
- **judge low agreement** — the judge disagreed with itself; that criterion was excluded.

## 7. Check the judged numbers are not doing the heavy lifting

Judged metrics are opinions with evidence attached. If the verdict would flip without them,
say so out loud. Open one `judge.json` and read the `evidence` arrays: they must cite real
`file:line` locations from the patch.

## 8. Spot-check one run end to end

Pick the task that most influenced the verdict:

```bash
agent-eval show <task>-candidate-rep01 -c <config> --diff
```

Read the patch and decide for yourself whether it satisfies the task. If you disagree with
the tool on a run you have read, do not ship the verdict.

## 9. Ask what the benchmark cannot see

These tasks are small, in one language, in a clean repository. Harness quality matters most
in large messy codebases. A positive result here is evidence about this benchmark, and only
weak evidence about your monorepo.

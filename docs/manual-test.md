# Manual test plan

Every command is copy/pasteable. Total time ~5 minutes, no API key, no spend.

## 1. Install

```bash
cd agent-eval
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
agent-eval version                 # → agent-eval 0.1.0
```

## 2. Run the automated suite

```bash
pytest                             # 176 passed (~2.5 min), no network access
ruff check src/ tests/             # All checks passed!
mypy src/agent_eval                # Success: no issues found
```

## 3. Inspect the benchmark

```bash
agent-eval tasks -b benchmarks/py-customers
```
Expect five tasks, each with `tests, lint, types` and a reference; t05 additionally has
`changed_src`.

## 4. Validate the benchmark (the golden check)

```bash
agent-eval doctor -b benchmarks/py-customers
```
Expect `All 5 tasks valid.` in ~7 seconds, exit code 0.

## 5. Prove the golden check is real, not decorative

**5a — make a task vacuous.** Give the fixture the fix t02 asks for:

```bash
python - <<'PY'
import pathlib
p = pathlib.Path("benchmarks/py-customers/fixture/src/customers/service.py")
p.write_text(p.read_text().replace(
    'return self._repository.find(customer_id)',
    'return self._repository.get(customer_id)'))
PY
agent-eval doctor -b benchmarks/py-customers -t t02-fix-lookup-bug
```
Expect `BROKEN`, `VACUOUS`, exit code 1. Then undo:
```bash
git checkout benchmarks/py-customers/fixture/src/customers/service.py
```

**5b — make a task unsolvable.**
```bash
echo '

def test_impossible():
    assert 1 == 2' >> benchmarks/py-customers/tasks/t01-export-csv/verify/test_t01.py
agent-eval doctor -b benchmarks/py-customers -t t01-export-csv -v
git checkout benchmarks/py-customers/tasks/t01-export-csv/verify/test_t01.py
```
Expect `BROKEN`, `UNSOLVABLE`, exit code 1.

## 6. Run the offline evaluation

```bash
agent-eval evaluate --config examples/demo.yaml
```
Expect 30 runs in ~35 seconds, a verdict, a metrics table, gate-by-gate reasons, and a
`MANUAL REVIEW REQUIRED` block.

## 7. Open the report

```bash
open runs/eval-*/report.html          # macOS; xdg-open on Linux
```

Check by eye:
- the dashed **SIMULATED AGENT** banner near the top;
- §2 says `MODEL: unchanged` — the comparison is controlled;
- §3 tags every row `measured` or `judged`, and the judged row is visually distinct;
- §3 states the resolving power (~20 percentage points);
- §6's matrix has **three** columns and the highlighted cells match §7's queue exactly;
- §9 Limitations is a numbered section, not a footnote.

## 8. Inspect raw evidence

```bash
ls runs/eval-*/runs/t02-fix-lookup-bug/candidate/rep-01/
cat runs/eval-*/runs/t02-fix-lookup-bug/candidate/rep-01/diff.patch
agent-eval show t02-fix-lookup-bug-candidate-rep01 -c examples/demo.yaml --diff
```

## 9. Verify one result by hand

Pick a run the report says **failed** on t02 and confirm the tool is right:

```bash
cat runs/eval-*/runs/t02-fix-lookup-bug/baseline/rep-01/diff.patch
```
If the patch raises a builtin `ValueError` rather than the package's `NotFoundError`, then
`verify/test_t02.py::test_raised_error_is_an_app_error` must fail — check:
```bash
jq -r '.[] | select(.id=="tests") | .stdout' \
   runs/eval-*/runs/t02-fix-lookup-bug/baseline/rep-01/checks.json | tail -20
```

## 10. Prove the harness never leaks into the patch

```bash
grep -l "AGENTS.md" runs/eval-*/runs/*/*/rep-*/diff.patch ; echo "exit=$? (1 = nothing found, correct)"
```

## 11. Prove the report rebuilds without re-running agents

```bash
cp examples/demo.yaml /tmp/strict.yaml
sed -i '' 's/cost_tolerance: 0.15/cost_tolerance: 0.90/' /tmp/strict.yaml   # Linux: drop the ''
agent-eval report -c /tmp/strict.yaml
```
The cost guardrail should stop firing, the verdict may change, and it should take
milliseconds. The stored runs are untouched.

## 12. Prove the SQLite index is disposable

```bash
rm runs/index.sqlite
agent-eval index -o runs        # → indexed 30 runs
```

## 13. Re-judge without re-running agents

```bash
agent-eval judge -c examples/demo.yaml --provider mock
```
Judge scores change; the runs, patches and check results do not.

## 14. Against a real agent (optional — costs money)

**14a — find the `claude` binary.** `evaluate` fails fast if it is not on `PATH`:

```bash
which claude || ls -l ~/.claude/local/claude /opt/homebrew/bin/claude \
  /usr/local/bin/claude ~/.local/bin/claude 2>/dev/null
```

If it exists but is not on `PATH`, point the config at it rather than editing your shell:

```yaml
agent:
  adapter: claude-code
  executable: /Users/you/.claude/local/claude
```

**14b — check it speaks JSON before spending anything.** The adapter parses
`--output-format json` for usage and cost; if your build does not support it, every run
records `unavailable` rather than failing, but you want to know that up front:

```bash
cd /tmp && claude -p "reply with the single word PONG" --output-format json | head -5
```

**14c — smoke-test with two runs, not thirty.**

```bash
agent-eval evaluate --config examples/real.yaml --task t01-export-csv --runs 1
```

Two agent runs, a couple of minutes, a few cents. Then inspect one before committing to the
full set — this is where you find out whether the agent could actually work in the prepared
workspace:

```bash
agent-eval show t01-export-csv-baseline-rep01 -c examples/real.yaml --diff
```

Check: a non-empty patch, `exit 0`, and `usage actual:` with real token counts. An empty
patch with exit 0 usually means the agent hit a permission or trust prompt — set
`permission_mode` in both `harness.yaml` files, or add `extra_args` there.

**14d — the full run.**

```bash
agent-eval evaluate --config examples/real.yaml
```

~30 real runs. Read [Privacy and security](../README.md#privacy-and-security) first if the
judge is enabled — it sends your patches to a third-party model.

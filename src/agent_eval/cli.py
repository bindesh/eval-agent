"""The agent-eval command line.

Each command corresponds to one stage of the pipeline and can be run on its own against
stored artifacts. `evaluate` is the one that does everything.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .benchmark import BenchmarkError, load_benchmark
from .config import Config
from .decide import Decision
from .doctor import diagnose
from .execution import build_plan, execute_run
from .harness import HarnessError, diff_harnesses, snapshot_harness
from .judge import Judge, build_provider
from .judging import judge_all
from .metrics import Comparison
from .pipeline import finalise
from .runners import ClaudeCodeRunner, ReplayRunner, SimulatedRunner
from .store import EvaluationStore, new_evaluation_id, rebuild_index

app = typer.Typer(
    add_completion=False,
    help="Evidence for whether a coding-agent harness change actually helped.",
)
console = Console()

# Three in a row means the environment is broken, not that the agent is bad.
MAX_CONSECUTIVE_INVALID = 3

VERDICT_STYLE = {
    "POSITIVE": "bold green", "NEGATIVE": "bold red",
    "INCONCLUSIVE": "bold yellow", "NOT_COMPARABLE": "bold white",
}


def _fail(message: str, code: int = 2) -> NoReturn:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code)


def _build_runner(config: Config, benchmark_dir: Path):
    """Pick the adapter. Kept in one place so `evaluate` never branches on it."""
    agent = config.agent
    if agent.adapter == "claude-code":
        runner = ClaudeCodeRunner(executable=agent.executable)
        if not runner.available():
            _fail(
                f"agent executable {agent.executable!r} not found on PATH.\n"
                f"Use --agent simulated for the offline demo, or --agent replay with "
                f"recorded cassettes."
            )
        return runner
    if agent.adapter == "replay":
        if not agent.cassette_dir:
            _fail("agent.cassette_dir must be set when adapter is 'replay'")
        return ReplayRunner(agent.cassette_dir)
    return SimulatedRunner(benchmark_dir, seed=config.evaluation.seed)


def _print_verdict(decision: Decision, comparison: Comparison) -> None:
    style = VERDICT_STYLE.get(decision.verdict, "bold")
    console.print()
    console.print(f"[{style}]{decision.verdict}[/{style}]  {decision.headline}")
    console.print()

    table = Table(header_style="bold", title="Metrics (paired over tasks)")
    table.add_column("metric")
    table.add_column("kind")
    table.add_column("baseline", justify="right")
    table.add_column("candidate", justify="right")
    table.add_column("delta", justify="right")
    table.add_column("95% CI")
    def _format(value: float | None, metric: str) -> str:
        """Bound to `metric` explicitly: a closure over the loop variable would format
        every row with the last metric's units."""
        if value is None:
            return "n/a"
        if metric in {"correctness", "quality", "reliability"}:
            return f"{value * 100:.1f}%"
        if metric == "cost":
            return f"${value:.4f}"
        if metric == "duration":
            return f"{value:.1f}s"
        return f"{value:.2f}"

    for delta in comparison.deltas.values():
        fmt = lambda value: _format(value, delta.metric)  # noqa: B023,E731

        if delta.absolute is None:
            change, colour = "n/a", "dim"
        else:
            change = fmt(delta.absolute) if delta.metric not in {
                "correctness", "quality", "reliability"
            } else f"{delta.absolute * 100:+.1f}pp"
            colour = "green" if delta.improved else ("dim" if delta.absolute == 0 else "red")
        interval = (
            f"[{delta.ci_low:+.3f}, {delta.ci_high:+.3f}]"
            + ("  excludes 0" if delta.significant else "")
            if delta.ci_low is not None else "n/a"
        )
        table.add_row(
            delta.label, "measured" if delta.objective else "[magenta]judged[/magenta]",
            fmt(delta.baseline), fmt(delta.candidate),
            f"[{colour}]{change}[/{colour}]", interval,
        )
    console.print(table)

    console.print("\n[bold]Why[/bold]")
    for reason in decision.reasons:
        colour = {"critical": "red", "warning": "yellow"}.get(reason.severity, "dim")
        console.print(f"  [{colour}]{reason.gate:<14}[/{colour}] {reason.detail}")

    if decision.manual_review_required:
        console.print("\n[bold yellow]MANUAL REVIEW REQUIRED[/bold yellow]")
        for item in decision.manual_review_reasons:
            console.print(f"  - {item}")


# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the tool version."""
    console.print(f"agent-eval {__version__}")


@app.command()
def tasks(
    benchmark: Path = typer.Option(..., "--benchmark", "-b", help="Benchmark directory"),
) -> None:
    """List the tasks in a benchmark."""
    try:
        spec = load_benchmark(benchmark)
    except BenchmarkError as exc:
        _fail(f"benchmark error: {exc}")
    table = Table(title=f"{spec.title}  ({len(spec.tasks)} tasks)", header_style="bold")
    for column in ("id", "title", "property", "checks"):
        table.add_column(column)
    table.add_column("ref", justify="center")
    for task in spec.tasks:
        table.add_row(
            task.id, task.title, task.measures, ", ".join(c.id for c in task.checks),
            "[green]yes[/green]" if task.has_reference else "[red]no[/red]",
        )
    console.print(table)


@app.command()
def doctor(
    benchmark: Path = typer.Option(..., "--benchmark", "-b"),
    task: list[str] = typer.Option(None, "--task", "-t", help="Limit to these task ids"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show failing check output"),
) -> None:
    """Validate a benchmark before trusting any evaluation run against it.

    Asserts, for every task, that verification FAILS on the untouched fixture (the task is
    not vacuous), that the objective quality checks PASS there (so a later lint failure is
    attributable to the agent), and that everything PASSES with the stored reference
    solution (the task is solvable and the checks are correct).
    """
    try:
        spec = load_benchmark(benchmark)
    except BenchmarkError as exc:
        _fail(f"benchmark error: {exc}")

    console.print(f"Validating [bold]{spec.id}[/bold] with {sys.executable}\n")
    results = diagnose(spec, task_ids=list(task) if task else None)

    table = Table(header_style="bold")
    for column in ("task", "fails on pristine", "quality clean", "reference passes", "verdict"):
        table.add_column(column, justify="center" if column != "task" else "left")
    mark = lambda ok: "[green]yes[/green]" if ok else "[red]NO[/red]"  # noqa: E731
    for result in results:
        table.add_row(
            result.task_id, mark(result.correctness_fails_on_pristine),
            mark(result.quality_clean_on_pristine), mark(result.reference_passes),
            "[green]OK[/green]" if result.ok else "[red]BROKEN[/red]",
        )
    console.print(table)

    broken = [r for r in results if not r.ok]
    for result in broken:
        console.print(f"\n[red]{result.task_id}[/red]")
        for problem in result.problems():
            console.print(f"  - {problem}")
        if verbose:
            for outcome in result.reference:
                if not outcome.passed:
                    console.print(f"\n  [dim]reference / {outcome.id}[/dim]")
                    console.print((outcome.stdout or outcome.stderr)[-2000:])
    if broken:
        console.print(f"\n[red]{len(broken)} of {len(results)} tasks are broken.[/red]")
        raise typer.Exit(1)
    console.print(f"\n[green]All {len(results)} tasks valid.[/green]")


@app.command()
def evaluate(
    config_path: Path = typer.Option(None, "--config", "-c", help="YAML config file"),
    baseline: Path = typer.Option(None, "--baseline", help="Baseline harness directory"),
    candidate: Path = typer.Option(None, "--candidate", help="Candidate harness directory"),
    benchmark: Path = typer.Option(None, "--benchmark", "-b"),
    runs: int = typer.Option(None, "--runs", "-n", help="Repetitions per task per harness"),
    agent: str = typer.Option(None, "--agent", help="claude-code | replay | simulated"),
    model: str = typer.Option(None, "--model"),
    task: list[str] = typer.Option(None, "--task", "-t"),
    judge: bool = typer.Option(None, "--judge/--no-judge", help="Enable the LLM judge"),
    output: Path = typer.Option(None, "--output", "-o", help="Where evaluations are stored"),
    skip_doctor: bool = typer.Option(False, "--skip-doctor", help="Do not validate first"),
    keep_workspaces: bool = typer.Option(False, "--keep-workspaces"),
) -> None:
    """Run the full evaluation: both harnesses, every task, N repetitions, then report."""
    if config_path:
        config = Config.load(config_path)
    else:
        if not (baseline and candidate and benchmark):
            _fail("provide --config, or all of --baseline, --candidate and --benchmark")
        config = Config(evaluation={
            "name": f"{Path(baseline).name} vs {Path(candidate).name}",
            "benchmark": Path(benchmark).resolve(),
            "baseline": Path(baseline).resolve(), "candidate": Path(candidate).resolve(),
        })
    if runs is not None:
        config.evaluation.runs = runs
    if agent is not None:
        config.agent.adapter = agent  # type: ignore[assignment]
    if model is not None:
        config.agent.model = model
    if task:
        config.evaluation.tasks = list(task)
    if judge is not None:
        config.judge.enabled = judge
    if output is not None:
        config.evaluation.output_dir = Path(output).resolve()

    try:
        spec = load_benchmark(config.evaluation.benchmark)
        base_snapshot = snapshot_harness(config.evaluation.baseline, name="baseline")
        cand_snapshot = snapshot_harness(config.evaluation.candidate, name="candidate")
    except (BenchmarkError, HarnessError) as exc:
        _fail(str(exc))

    selected = config.evaluation.tasks or [t.id for t in spec.tasks]

    # Validating the benchmark before spending money on agent runs is the cheapest
    # possible insurance: a vacuous or unsolvable task silently dilutes every result.
    if not skip_doctor:
        console.print("[dim]Validating benchmark...[/dim]")
        broken = [r for r in diagnose(spec, task_ids=selected) if not r.ok]
        if broken:
            for result in broken:
                console.print(f"[red]{result.task_id}[/red]: {'; '.join(result.problems())}")
            _fail("benchmark is not valid; fix it or pass --skip-doctor", 1)

    harness_diff = diff_harnesses(base_snapshot, cand_snapshot)
    if harness_diff.identical:
        console.print("[yellow]warning: the two harnesses are byte-identical[/yellow]")

    store = EvaluationStore(config.evaluation.output_dir, new_evaluation_id()).create()
    plan = build_plan(
        selected, config.evaluation.runs,
        order=config.evaluation.order, seed=config.evaluation.seed,
    )
    store.write_metadata(
        config=config.model_dump(mode="json"), baseline=base_snapshot,
        candidate=cand_snapshot, extra={"harness_diff": harness_diff.model_dump(mode="json")},
    )
    store.write_plan([item.as_dict() for item in plan])

    runner = _build_runner(config, spec.directory)

    # Construct the judge now, not after the agent runs. It validates its dependencies,
    # its credentials and its prompt version on construction - and discovering a missing
    # package or an unset API key after thirty paid agent runs is an expensive way to
    # learn it. Everything that can fail cheaply should fail before anything expensive
    # starts.
    judge_impl: Judge | None = None
    if config.judge.enabled:
        try:
            judge_impl = Judge(
                build_provider(config.judge.provider), model=config.judge.model,
                prompt_version=config.judge.prompt_version,
                self_consistency=config.judge.self_consistency,
                temperature=config.judge.temperature,
                max_patch_chars=config.judge.max_patch_chars,
            )
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            _fail(f"judge is enabled but cannot be constructed: {exc}\n"
                  f"Run with --no-judge to evaluate the objective signals only.")

    console.print(
        f"[bold]{config.evaluation.name}[/bold]\n"
        f"  baseline  {base_snapshot.short_id}\n"
        f"  candidate {cand_snapshot.short_id}\n"
        f"  {len(selected)} tasks x {config.evaluation.runs} reps x 2 arms = "
        f"{len(plan)} runs  ({config.evaluation.order}, adapter={config.agent.adapter})\n"
    )

    snapshots = {"baseline": base_snapshot, "candidate": cand_snapshot}
    consecutive_invalid = 0
    for item in plan:
        record = execute_run(
            benchmark=spec, task=spec.task(item.task_id), harness=snapshots[item.arm],
            item=item, runner=runner, store=store, model=config.agent.model,
            timeout_seconds=config.agent.timeout_seconds, seed=config.evaluation.seed,
            keep_workspace=keep_workspaces,
        )
        if record.invalid:
            status = "[yellow]did not run[/yellow]"
        elif record.correctness_passed:
            status = "[green]pass[/green]"
        else:
            status = "[red]fail[/red]"
        tamper = " [yellow]tamper[/yellow]" if record.tamper.detected else ""
        console.print(
            f"  [{item.order_index + 1:>2}/{len(plan)}] {item.task_id:<24} "
            f"{item.arm:<10} rep{item.rep} {status}{tamper} "
            f"[dim]{record.duration_s:.1f}s[/dim]"
        )

        # An environment problem repeats. Burning the remaining runs to collect more
        # copies of the same error costs money and produces nothing.
        consecutive_invalid = consecutive_invalid + 1 if record.invalid else 0
        if record.invalid:
            console.print(f"      [yellow]{record.invalid_reason}[/yellow]")
        if consecutive_invalid >= MAX_CONSECUTIVE_INVALID:
            console.print(
                f"\n[red]Stopping: {consecutive_invalid} runs in a row never executed.[/red]\n"
                f"[yellow]{record.invalid_reason}[/yellow]\n"
                f"This is an environment problem, not a result. Nothing has been scored. "
                f"Fix it and re-run; the runs completed so far are in {store.dir}."
            )
            raise typer.Exit(3)

    if judge_impl is not None:
        console.print("\n[dim]Judging patches (blind to arm, harness and test results)...[/dim]")
        try:
            judge_all(judge_impl, spec, store)
        except Exception as exc:  # noqa: BLE001
            # The judged signal is one dimension of five, and the agent runs are the
            # expensive part. Losing the whole report - and the objective evidence in
            # it - because an optional stage failed would be the wrong trade.
            console.print(
                f"[yellow]judging failed ({type(exc).__name__}: {exc}).[/yellow]\n"
                f"[yellow]Continuing with the objective signals only; re-run judging "
                f"later with:  agent-eval judge -c <config>[/yellow]"
            )

    comparison, decision, _, json_path, html_path = finalise(
        store, config, benchmark=spec, baseline=base_snapshot, candidate=cand_snapshot
    )
    rebuild_index(config.evaluation.output_dir)
    _print_verdict(decision, comparison)
    console.print(f"\n  report  {html_path}\n  json    {json_path}")


@app.command(name="judge")
def judge_command(
    config_path: Path = typer.Option(..., "--config", "-c"),
    evaluation: str = typer.Option(None, "--evaluation", "-e", help="Defaults to the latest"),
    provider: str = typer.Option(None, "--provider", help="anthropic | mock"),
) -> None:
    """(Re-)judge the stored patches of an evaluation, without re-running any agent."""
    config = Config.load(config_path)
    store = (
        EvaluationStore(config.evaluation.output_dir, evaluation)
        if evaluation else EvaluationStore.open_latest(config.evaluation.output_dir)
    )
    spec = load_benchmark(config.evaluation.benchmark)
    judge_impl = Judge(
        build_provider(provider or config.judge.provider), model=config.judge.model,
        prompt_version=config.judge.prompt_version,
        self_consistency=config.judge.self_consistency, temperature=config.judge.temperature,
    )
    records = judge_all(
        judge_impl, spec, store,
        progress=lambda r: console.print(f"  judged {r.run_id}"),
    )
    console.print(f"[green]judged {len(records)} runs[/green]")
    comparison, decision, _, _, html = finalise(store, config, benchmark=spec)
    _print_verdict(decision, comparison)
    console.print(f"\n  report  {html}")


@app.command()
def report(
    config_path: Path = typer.Option(..., "--config", "-c"),
    evaluation: str = typer.Option(None, "--evaluation", "-e", help="Defaults to the latest"),
) -> None:
    """Rebuild the report from stored artifacts.

    Change a threshold in the config and run this: the verdict is recomputed from the same
    evidence in milliseconds, with no agent and no spend.
    """
    config = Config.load(config_path)
    store = (
        EvaluationStore(config.evaluation.output_dir, evaluation)
        if evaluation else EvaluationStore.open_latest(config.evaluation.output_dir)
    )
    comparison, decision, _, json_path, html_path = finalise(store, config)
    _print_verdict(decision, comparison)
    console.print(f"\n  report  {html_path}\n  json    {json_path}")


@app.command()
def index(
    output: Path = typer.Option(Path("runs"), "--output", "-o", help="Evaluations directory"),
) -> None:
    """Rebuild the SQLite index from the artifact files. Always safe; never authoritative."""
    count = rebuild_index(output)
    console.print(f"[green]indexed {count} runs[/green] into {output}/index.sqlite")


@app.command()
def show(
    run_id: str = typer.Argument(..., help="Run id, e.g. t02-fix-lookup-bug-candidate-rep01"),
    config_path: Path = typer.Option(..., "--config", "-c"),
    evaluation: str = typer.Option(None, "--evaluation", "-e"),
    diff: bool = typer.Option(False, "--diff", help="Print the patch"),
) -> None:
    """Inspect one stored run — the bottom of the evidence chain."""
    config = Config.load(config_path)
    store = (
        EvaluationStore(config.evaluation.output_dir, evaluation)
        if evaluation else EvaluationStore.open_latest(config.evaluation.output_dir)
    )
    match = next((r for r in store.read_runs() if r.run_id == run_id), None)
    if match is None:
        _fail(f"no run {run_id!r} in {store.dir}")

    console.print(f"[bold]{match.run_id}[/bold]  {match.arm}  harness {match.harness_short_id}")
    console.print(f"  adapter   {match.agent_adapter}  model {match.model_reported or match.model}")
    console.print(f"  duration  {match.duration_s:.1f}s   exit {match.exit_code}")
    console.print(f"  usage     {match.usage.source}: {match.usage.total_tokens} tokens, "
                  f"cost {match.usage.cost_usd}")
    console.print(f"  changed   {', '.join(match.changed_files) or '(nothing)'}")
    if match.tamper.detected:
        console.print(f"  [yellow]tamper    {', '.join(match.tamper.paths)}[/yellow]")
    for outcome in match.checks:
        colour = "green" if outcome.passed else "red"
        console.print(f"  [{colour}]{outcome.id:<12} {outcome.summary}[/{colour}]  "
                      f"[dim]{' '.join(outcome.command[:4])}[/dim]")
    if match.judge:
        console.print(f"  [magenta]judge     {match.judge.score} "
                      f"({match.judge.model}, prompt {match.judge.prompt_version})[/magenta]")
    console.print(f"  evidence  {store.dir / match.artifacts.get('dir', '')}")
    if diff:
        console.print("\n[dim]--- patch ---[/dim]")
        console.print(store.read_diff(match) or "(empty)")


if __name__ == "__main__":
    app()

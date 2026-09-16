"""Applying the judge to stored runs.

This is a separate stage on purpose. It reads run artifacts from disk and writes judge
artifacts back, so judging can be re-run, re-run with a different prompt version, or
skipped entirely, without touching an agent. Re-judging thirty stored patches costs
cents; re-running thirty agents costs dollars and forty minutes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .judge import Judge, JudgeResult
from .models import BenchmarkSpec, JudgeSummary, RunRecord
from .store import EvaluationStore

MAX_CONTEXT_CHARS = 6_000


def build_repo_context(benchmark: BenchmarkSpec, record: RunRecord) -> str:
    """Assemble what the judge is allowed to know about the repository.

    Two things are deliberately absent, and their absence is the point:

    * **no harness content** - the judge must not be able to infer which arm it is
      looking at, and a patch produced under a harness that says "always use AppError"
      must not be scored against that instruction rather than against the codebase;
    * **no check results** - see the module docstring in `judge/base.py`.

    What it does get is the fixture's own README and the pre-change state of the files the
    patch touched, which is what a human reviewer would open first.
    """
    parts: list[str] = []
    readme = benchmark.fixture_dir / "README.md"
    if readme.exists():
        parts.append(f"# README.md\n{readme.read_text()}")

    for relative in record.changed_files[:4]:
        original = benchmark.fixture_dir / relative
        if not original.exists() or original.suffix not in {".py", ".md", ".toml"}:
            continue
        body = original.read_text()
        if len(body) > 2_500:
            body = body[:2_500] + "\n... [truncated] ...\n"
        parts.append(f"# {relative} (before the patch)\n{body}")

    context = "\n\n".join(parts)
    return context[:MAX_CONTEXT_CHARS]


def judge_run(
    judge: Judge, benchmark: BenchmarkSpec, store: EvaluationStore, record: RunRecord
) -> JudgeResult:
    """Judge one stored run and persist the full raw result next to it."""
    task = benchmark.task(record.task_id)
    result = judge.evaluate(
        task_prompt=task.prompt,
        patch=store.read_diff(record),
        repo_context=build_repo_context(benchmark, record),
        rubric=task.rubric,
        task_id=record.task_id,
        run_id=record.run_id,
    )

    directory = store.dir / record.artifacts["dir"]
    (directory / "judge.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2)
    )

    record.judge = JudgeSummary(
        score=result.score, criteria=result.criteria, confidence=result.confidence,
        low_agreement=result.low_agreement, model=result.model, provider=result.provider,
        prompt_version=result.prompt_version, error=result.error,
    )
    record.artifacts["judge"] = str(Path(record.artifacts["dir"]) / "judge.json")
    (directory / "run.json").write_text(json.dumps(record.model_dump(mode="json"), indent=2))
    return result


def judge_all(
    judge: Judge, benchmark: BenchmarkSpec, store: EvaluationStore,
    records: list[RunRecord] | None = None, *, progress=None,
) -> list[RunRecord]:
    """Judge every stored run, in order."""
    records = records if records is not None else store.read_runs()
    for record in records:
        judge_run(judge, benchmark, store, record)
        if progress is not None:
            progress(record)
    return records

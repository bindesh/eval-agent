"""The artifact store.

**Files are the source of truth; SQLite is a rebuildable index.**

A reviewer who distrusts a number in the report needs to walk back to the stdout log and
the diff that produced it. They can `cat` a file; they cannot `cat` a database. So every
run writes plain JSON and plain logs, and the database is a convenience for querying
across evaluations that can be deleted and regenerated at any time.
"""

from __future__ import annotations

import json
import platform
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .harness import HarnessSnapshot
from .models import RunRecord
from .redact import redact, safe_env_snapshot

INDEX_FILENAME = "index.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, evaluation_id TEXT, task_id TEXT, arm TEXT, rep INTEGER,
    harness_short_id TEXT, model TEXT, adapter TEXT, simulated INTEGER,
    duration_s REAL, cost_usd REAL, total_tokens INTEGER,
    correctness INTEGER, quality INTEGER, tamper INTEGER, path TEXT
);
CREATE INDEX IF NOT EXISTS runs_eval ON runs(evaluation_id);
CREATE INDEX IF NOT EXISTS runs_task ON runs(task_id);
"""


def new_evaluation_id() -> str:
    return "eval-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def environment_metadata() -> dict:
    """Recorded so a reader can tell whether two evaluations are comparable at all."""
    return {
        "tool_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "executable": sys.executable,
        "env": safe_env_snapshot(),
    }


class EvaluationStore:
    """Reads and writes one evaluation's artifact tree."""

    def __init__(self, root: Path, evaluation_id: str) -> None:
        self.root = Path(root)
        self.evaluation_id = evaluation_id
        self.dir = self.root / evaluation_id

    # -- layout -----------------------------------------------------------

    @property
    def runs_dir(self) -> Path:
        return self.dir / "runs"

    def run_dir(self, record_or_task, arm: str = "", rep: int = 0) -> Path:
        if isinstance(record_or_task, RunRecord):
            task_id, arm, rep = record_or_task.task_id, record_or_task.arm, record_or_task.rep
        else:
            task_id = record_or_task
        return self.runs_dir / task_id / arm / f"rep-{rep:02d}"

    def create(self) -> EvaluationStore:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        return self

    # -- writing ----------------------------------------------------------

    def write_metadata(
        self, *, config: dict, baseline: HarnessSnapshot, candidate: HarnessSnapshot, extra: dict
    ) -> Path:
        payload = {
            "evaluation_id": self.evaluation_id,
            "created_at": datetime.now(UTC).isoformat(),
            "config": config,
            "environment": environment_metadata(),
            "harnesses": {
                "baseline": baseline.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
            },
            **extra,
        }
        path = self.dir / "metadata.json"
        path.write_text(json.dumps(payload, indent=2, default=str))
        return path

    def write_plan(self, items: list[dict]) -> Path:
        path = self.dir / "plan.jsonl"
        path.write_text("".join(json.dumps(item) + "\n" for item in items))
        return path

    def write_run(self, record: RunRecord, *, stdout: str, stderr: str, diff: str,
                  raw: dict | None = None) -> RunRecord:
        """Persist one run and its raw evidence, redacting before anything hits disk."""
        directory = self.run_dir(record)
        directory.mkdir(parents=True, exist_ok=True)

        (directory / "stdout.log").write_text(redact(stdout))
        (directory / "stderr.log").write_text(redact(stderr))
        (directory / "diff.patch").write_text(diff)
        if raw is not None:
            (directory / "agent_raw.json").write_text(
                redact(json.dumps(raw, indent=2, default=str))
            )
        (directory / "checks.json").write_text(
            json.dumps([c.model_dump(mode="json") for c in record.checks], indent=2)
        )

        rel = directory.relative_to(self.dir)
        record.artifacts = {
            "dir": str(rel),
            "stdout": str(rel / "stdout.log"),
            "stderr": str(rel / "stderr.log"),
            "diff": str(rel / "diff.patch"),
            "checks": str(rel / "checks.json"),
            "run": str(rel / "run.json"),
        }
        (directory / "run.json").write_text(json.dumps(record.model_dump(mode="json"), indent=2))
        return record

    def write_json(self, name: str, payload: dict) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(payload, indent=2, default=str))
        return path

    def write_text(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text)
        return path

    # -- reading ----------------------------------------------------------

    def read_runs(self) -> list[RunRecord]:
        """Load every stored run. This is the entry point for every re-runnable stage."""
        records = [
            RunRecord(**json.loads(path.read_text()))
            for path in sorted(self.runs_dir.rglob("run.json"))
        ]
        return sorted(records, key=lambda r: (r.task_id, r.arm, r.rep))

    def read_metadata(self) -> dict:
        return json.loads((self.dir / "metadata.json").read_text())

    def read_diff(self, record: RunRecord) -> str:
        path = self.dir / record.artifacts.get("diff", "")
        return path.read_text() if path.exists() else ""

    @classmethod
    def open_latest(cls, root: Path) -> EvaluationStore:
        candidates = sorted(
            (d for d in Path(root).iterdir() if (d / "metadata.json").exists()),
            key=lambda d: d.name,
        )
        if not candidates:
            raise FileNotFoundError(f"no evaluations found under {root}")
        return cls(Path(root), candidates[-1].name)


def rebuild_index(root: Path) -> int:
    """Regenerate the SQLite index from the artifact files. Always safe to run."""
    root = Path(root)
    index = root / INDEX_FILENAME
    if index.exists():
        index.unlink()
    connection = sqlite3.connect(index)
    connection.executescript(SCHEMA)
    count = 0
    for run_json in sorted(root.rglob("runs/*/*/rep-*/run.json")):
        record = RunRecord(**json.loads(run_json.read_text()))
        connection.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.run_id, record.evaluation_id, record.task_id, record.arm, record.rep,
                record.harness_short_id, record.model, record.agent_adapter,
                int(record.simulated), record.duration_s, record.usage.cost_usd,
                record.usage.total_tokens, int(record.correctness_passed),
                int(record.quality_passed), int(record.tamper.detected), str(run_json),
            ),
        )
        count += 1
    connection.commit()
    connection.close()
    return count

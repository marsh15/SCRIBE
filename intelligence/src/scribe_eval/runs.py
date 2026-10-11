"""Run records (INV-11): everything needed to reproduce and attribute a score."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .metrics import CaseResult

DEFAULT_RUNS_DIR = Path("benchmarks/runs")


def git_commit(cwd: str | Path | None = None) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10, check=True
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def config_hash(name: str, config: dict[str, Any] | None = None) -> str:
    blob = json.dumps({"name": name, "config": config or {}}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def tool_versions() -> dict[str, str]:
    from importlib import metadata

    versions = {"python": sys.version.split()[0], "platform": platform.platform()}
    for pkg in ("scribe-intel", "pydantic"):
        try:
            versions[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            versions[pkg] = "unknown"
    return versions


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    dataset_version: str
    split: str
    profile_name: str
    profile_id: str | None = None  # retrieval_profiles.id (uuid) when known
    config_hash: str
    code_commit: str = Field(default_factory=git_commit)
    model_ids: dict[str, str] = {}
    prompt_versions: dict[str, str] = {}
    tool_versions: dict[str, str] = Field(default_factory=tool_versions)
    started_at: str = Field(default_factory=now_iso)
    finished_at: str | None = None
    summary: dict[str, Any] = {}
    # T28: a run built from fake generators/verifiers is never a measurement. ``stamp`` carries the visible marker.
    measurement: bool = True
    stamp: str | None = None
    dataset_sha256: str | None = None
    case_results: list[dict[str, Any]] = Field(default_factory=list, exclude=True)

    def write(self, out_dir: str | Path = DEFAULT_RUNS_DIR, results: list[CaseResult] | None = None) -> Path:
        """Write ``<out_dir>/<run_id>/run.json`` and ``results.jsonl``; returns the run dir."""
        if self.finished_at is None:
            self.finished_at = now_iso()
        run_dir = Path(out_dir) / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps(self.model_dump(), indent=2, default=str), encoding="utf-8")
        rows = self.case_results if results is None else [result_row(r) for r in results]
        (run_dir / "results.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
        return run_dir


def result_row(r: CaseResult) -> dict[str, Any]:
    return {
        "case_id": r.case_id, "category": r.category, "answerable": r.answerable,
        "metrics": r.metrics, "ranking": r.ranking, "latency_ms": r.latency_ms,
        "cost": r.cost, "degraded": r.degraded, "top_score": r.top_score,
    }


def load_run(run_dir: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return run, rows


# DESIGN B2 tables (created by the drizzle migration, not here).
_INSERT_RUN = (
    "INSERT INTO eval_runs (id, dataset_version, split, profile_id, config_hash, code_commit, started_at, finished_at) "
    "VALUES (%s::uuid, %s, %s, %s::uuid, %s, %s, %s, %s)"
)
_INSERT_RESULT = (
    "INSERT INTO eval_results (run_id, case_id, metrics, ranking, latency_ms, cost) "
    "VALUES (%s::uuid, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb)"
)


def persist_to_db(conn: Any, run: RunRecord, results: list[CaseResult] | None = None) -> None:
    """Insert the run and its per-case results using a psycopg connection (caller commits/owns it)."""
    rows = run.case_results if results is None else [result_row(r) for r in results]
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_RUN,
            (run.run_id, run.dataset_version, run.split, run.profile_id, run.config_hash,
             run.code_commit, run.started_at, run.finished_at or now_iso()),
        )
        for r in rows:
            latency = r.get("latency_ms")
            cur.execute(
                _INSERT_RESULT,
                (run.run_id, r["case_id"], json.dumps(r.get("metrics")), json.dumps(r.get("ranking")),
                 None if latency is None else round(latency), json.dumps(r.get("cost"))),
            )

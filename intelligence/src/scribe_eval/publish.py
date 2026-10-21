"""`scribe-eval publish-run`: load an on-disk run (``run.json`` + ``results.jsonl`` from ``score``, or ``run.json`` +
``answers.jsonl`` from ``answer``) into ``eval_runs`` / ``eval_results`` for the /eval dashboard (T30).

Idempotent: the run id is the primary key; re-publishing replaces the run's rows in one transaction.

Where things live (the B2 tables have no workspace column, eval runs measure the eval workspace's corpus):
  eval_runs.meta            kind (retrieval|answer), workspace_kind='eval', profile_name, model_ids, prompt_versions,
                            tool_versions, measurement, stamp, seeded, summary (means + 95% CIs), dataset_sha256.
  eval_results.metrics      the numeric metrics of the case, plus ``_case``: category, answerable, abstained,
                            question, failure (classifier output), clipped answer, claim statuses, trace_id.
  eval_results.ranking      top-10 candidates (clipped text, ``gold`` = matches gold evidence), retrieval runs only.
Only rows with ``meta.workspace_kind = 'eval'`` are ever shown by the dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .dataset import Case, load_cases
from .failures import UNCLASSIFIED, classify_failure
from .metrics import RankedItem, match_matrix
from .runs import now_iso

RANK_KEEP = 10
TEXT_CLIP = 300
ANSWER_CLIP = 600
FAKE_STAMP = "FAKE - NOT A MEASUREMENT"
SEEDED_STAMP = "SEEDED TEST DATA - NOT A MEASUREMENT"


class PublishError(Exception):
    pass


def _clip(text: Any, n: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= n else s[: n - 1] + "…"


def load_run_files(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Returns (run.json, case rows, kind)."""
    try:
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"cannot read {run_dir}/run.json: {exc}") from exc
    answers = run_dir / "answers.jsonl"
    results = run_dir / "results.jsonl"
    kind = "answer" if answers.is_file() else "retrieval"
    path = answers if kind == "answer" else results
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise PublishError(f"cannot read {path}: {exc}") from exc
    return run, rows, kind


def build_meta(run: dict[str, Any], kind: str, *, seeded: bool, mark_fake: bool, eval_workspace_id: str | None) -> dict[str, Any]:
    measurement = bool(run.get("measurement", True)) and not mark_fake and not seeded
    stamp = run.get("stamp")
    if seeded:
        stamp = SEEDED_STAMP
    elif mark_fake or not run.get("measurement", True):
        stamp = stamp or FAKE_STAMP
    return {
        "workspace_kind": "eval", "eval_workspace_id": eval_workspace_id, "kind": kind,
        "profile_name": run.get("profile_name"), "model_ids": run.get("model_ids") or {},
        "prompt_versions": run.get("prompt_versions") or {}, "tool_versions": run.get("tool_versions") or {},
        "measurement": measurement, "stamp": stamp if not measurement else None, "seeded": seeded,
        "summary": run.get("summary") or {}, "dataset_sha256": run.get("dataset_sha256"),
        "published_at": now_iso(),
    }


def result_record(row: dict[str, Any], case: Case | None, kind: str, parse_lost: list[str]) -> dict[str, Any]:
    """One eval_results row: (metrics jsonb, ranking jsonb, latency_ms, cost jsonb)."""
    metrics = {k: v for k, v in (row.get("metrics") or {}).items() if isinstance(v, (int, float)) or v is None}
    classify_row = dict(row)
    if parse_lost:
        classify_row["parse_lost_docs"] = parse_lost
    failure = classify_failure(case, classify_row) if case is not None else UNCLASSIFIED
    ranking = None
    flags: list[bool] = []
    if kind == "retrieval" and isinstance(row.get("ranking"), list):
        items = [RankedItem.from_dict(d) for d in row["ranking"][:RANK_KEEP] if isinstance(d, dict)]
        if case is not None and case.answerable and case.evidence:
            flags = [bool(m) for m in match_matrix(case, items)]
        ranking = [
            {"rank": i + 1, "chunk_id": it.chunk_id, "doc": it.doc, "score": it.score, "method": it.method,
             "text": _clip(it.text, TEXT_CLIP), "gold": flags[i] if i < len(flags) else None}
            for i, it in enumerate(items)
        ]
    claims = [{"text": _clip(c.get("text"), 200), "status": c.get("status")}
              for c in (row.get("claims") or []) if isinstance(c, dict)]
    metrics["_case"] = {
        "category": row.get("category") or (case.category if case else None),
        "answerable": row.get("answerable"), "abstained": row.get("abstained"),
        "question": case.question if case else None, "failure": failure,
        "answer": _clip(row.get("answer"), ANSWER_CLIP) if row.get("answer") is not None else None,
        "claims": claims or None, "bundle_state": row.get("bundle_state"), "trace_id": row.get("trace_id"),
        "degraded": row.get("degraded"),
    }
    return {"case_id": row["case_id"], "metrics": metrics, "ranking": ranking,
            "latency_ms": None if row.get("latency_ms") is None else round(row["latency_ms"]),
            "cost": row.get("cost")}


def publish_run(conn: Any, run_dir: str | Path, *, dataset_dir: str | Path | None = None, seeded: bool = False,
                mark_fake: bool = False, parse_lost: list[str] | None = None,
                eval_workspace_id: str | None = None) -> dict[str, Any]:
    """Insert/replace the run in eval_runs/eval_results on a psycopg connection (caller commits). Returns a summary."""
    run, rows, kind = load_run_files(Path(run_dir))
    cases: dict[str, Case] = {}
    if dataset_dir is not None:
        cases = {c.id: c for c in load_cases(Path(dataset_dir) / "cases.jsonl")}
    meta = build_meta(run, kind, seeded=seeded, mark_fake=mark_fake, eval_workspace_id=eval_workspace_id)
    records = [result_record(r, cases.get(r["case_id"]), kind, parse_lost or []) for r in rows]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO eval_runs (id, dataset_version, split, profile_id, config_hash, code_commit, meta, started_at, "
            "finished_at) VALUES (%s::uuid, %s, %s, %s::uuid, %s, %s, %s::jsonb, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET dataset_version = EXCLUDED.dataset_version, split = EXCLUDED.split, "
            "profile_id = EXCLUDED.profile_id, config_hash = EXCLUDED.config_hash, code_commit = EXCLUDED.code_commit, "
            "meta = EXCLUDED.meta, started_at = EXCLUDED.started_at, finished_at = EXCLUDED.finished_at",
            (run["run_id"], run["dataset_version"], run["split"], run.get("profile_id"), run.get("config_hash"),
             run.get("code_commit"), json.dumps(meta), run.get("started_at") or now_iso(),
             run.get("finished_at") or now_iso()))
        cur.execute("DELETE FROM eval_results WHERE run_id = %s::uuid", (run["run_id"],))
        for r in records:
            cur.execute(
                "INSERT INTO eval_results (run_id, case_id, metrics, ranking, latency_ms, cost) "
                "VALUES (%s::uuid, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb)",
                (run["run_id"], r["case_id"], json.dumps(r["metrics"]), json.dumps(r["ranking"]),
                 r["latency_ms"], json.dumps(r["cost"])))
    return {"run_id": run["run_id"], "kind": kind, "cases": len(records), "measurement": meta["measurement"],
            "stamp": meta["stamp"], "seeded": seeded}


def find_eval_workspace(conn: Any) -> str | None:
    row = conn.execute("SELECT id FROM workspaces WHERE kind = 'eval' ORDER BY created_at LIMIT 1").fetchone()
    return str(row[0]) if row else None


def reviewed_refusal(run_dir: str | Path, dataset_dir: str | Path) -> str | None:
    """A refusal message when the run's split holds any case that is not human-reviewed, else None.

    Not applied to ``--seeded`` / ``--mark-fake`` publishes, which are labelled as non-measurements.
    """
    from .review.require import refusal_message, unreviewed_ids

    run, _, _ = load_run_files(Path(run_dir))
    cases = load_cases(Path(dataset_dir) / "cases.jsonl")
    split = run.get("split")
    in_split = [c for c in cases if split is None or c.split == split]
    unreviewed = unreviewed_ids(in_split)
    if not unreviewed:
        return None
    return refusal_message(unreviewed, len(in_split), f"publish a run on split {split!r} as a measurement") + \
        " (or pass --seeded / --mark-fake to publish it labelled as a non-measurement)"


def run(args: Any) -> int:
    import sys

    import psycopg

    if not args.database_url:
        print("ERROR: --database-url is required (the environment is never consulted)", file=sys.stderr)
        return 2
    if not (args.seeded or args.mark_fake):
        try:
            refusal = reviewed_refusal(args.run_dir, args.dataset)
        except (PublishError, OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if refusal:
            print(refusal, file=sys.stderr)
            return 2
    try:
        with psycopg.connect(args.database_url) as conn:
            wid = find_eval_workspace(conn)
            summary = publish_run(conn, args.run_dir, dataset_dir=args.dataset, seeded=args.seeded,
                                  mark_fake=args.mark_fake, parse_lost=args.parse_lost, eval_workspace_id=wid)
            conn.commit()
    except (PublishError, OSError, ValueError, psycopg.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"published {summary['kind']} run {summary['run_id']} ({summary['cases']} cases); "
          f"measurement={summary['measurement']}" + (f"; {summary['stamp']}" if summary["stamp"] else ""))
    return 0

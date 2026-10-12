"""T22: move existing Sources onto the structural-v1 index, then (separately, by hand) retire legacy storage.

    python -m scribe_ingest.upgrade backfill [--dry-run] [--batch-size 50] [--workspace UUID] [--after-id N]
                                             [--max-sources N] [--requeue-failed] [--json]
    python -m scribe_ingest.upgrade progress [--workspace UUID] [--json]

`backfill` queues exactly ONE `ingestion_jobs` row per active, non-deleted Source version (files.active_version_id)
that has no structural-v1 generation yet and whose original blob is available (files.storage_url). It does NOT embed
anything itself: the existing T18 machinery (`/api/internal/ingest/tick`, `ingest_steps.run_step`) drains the queue, so
leases, fencing, retry taxonomy, budgets and usage events are all reused. See `ingest_steps` for the legacy-v1 branch
(version 1 has no recorded sha256; its original is downloaded from files.storage_url).

Design points:
- Idempotent: the unique index (file_id, source_version_id) plus ON CONFLICT DO NOTHING; a re-run queues nothing new.
- Resumable: all state lives in the database; every batch is one transaction. A killed run loses at most the
  in-flight batch. `--after-id` only skips already-scanned rows.
- Sources with no blob, or whose upgrade job failed with INVALID_DOCUMENT (blob 404/unreadable/corrupt), keep their
  legacy generation ACTIVE (still searchable through the legacy-dense strategy) and are marked
  files.reupload_required_at ("Re-upload to upgrade" in the UI). A new upload gives them structural-v1 normally.
- Eval workspaces (kind = 'eval') are skipped: the legacy-dense baseline lives there on purpose (D20).
- Needs a privileged (owner / BYPASSRLS) connection, like `scribe-eval ingest-corpus`; never run it as scribe_app.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import psycopg

from .indexer import INDEX_PROFILE_NAME

# A permanent job failure with one of these codes means the original cannot be read: ask for a re-upload.
REUPLOAD_ERROR_CODES = frozenset({"INVALID_DOCUMENT"})
STATES = ("upgraded", "building", "pending", "no_blob", "reupload", "failed", "stalled", "newer_upload")

# One classification for both `backfill` and `progress`. `%(profile)s` is the structural index profile name.
_SCAN_SQL = """
select f.id, f.workspace_id, f.active_version_id, f.reupload_reason, j.id as job_id, j.error_code,
  case
    when u.upgraded then 'upgraded'
    when f.desired_version_id is distinct from f.active_version_id then 'newer_upload'
    when j.status in ('queued', 'processing', 'retrying') then 'building'
    when f.reupload_required_at is not null then 'reupload'
    when j.status = 'failed' then 'failed'
    when j.id is not null then 'stalled'
    when coalesce(btrim(f.storage_url), '') = '' then 'no_blob'
    else 'pending'
  end as state
from files f
join workspaces w on w.id = f.workspace_id
left join lateral (
  select exists (
    select 1 from index_generations g join index_profiles ip on ip.id = g.index_profile_id
    where g.source_version_id = f.active_version_id and ip.name = %(profile)s and g.status in ('ACTIVE', 'READY')
  ) as upgraded
) u on true
left join lateral (
  select id, status, error_code from ingestion_jobs
  where file_id = f.id and source_version_id = f.active_version_id order by id desc limit 1
) j on true
where f.deleted_at is null and f.active_version_id is not null and w.kind <> 'eval'
  and f.id > %(after)s and (%(workspace)s::uuid is null or f.workspace_id = %(workspace)s::uuid)
order by f.id
"""

_INSERT_JOB = ("insert into ingestion_jobs(file_id, status, source_version_id) values (%s, 'queued', %s) "
               "on conflict do nothing")
_RESET_JOB = ("update ingestion_jobs set status = 'queued', stage = null, attempts = 0, error_code = null, "
              "last_error = null, next_retry_at = null, lease_token = null, lease_expires_at = null, "
              "finished_at = null, updated_at = now() "
              "where id = %s and status in ('failed', 'completed', 'cancelled', 'superseded')")
_MARK_REUPLOAD = ("update files set reupload_required_at = now(), reupload_reason = %s "
                  "where id = %s and reupload_required_at is null")


@dataclass
class BackfillReport:
    dry_run: bool
    batches: int = 0
    scanned: int = 0
    last_id: int = 0
    # what was (or, in a dry run, would be) done
    queued: int = 0
    requeued: int = 0
    marked_reupload: int = 0
    # what was left alone, by state
    untouched: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"dry_run": self.dry_run, "batches": self.batches, "scanned": self.scanned, "last_id": self.last_id,
                "queued": self.queued, "requeued": self.requeued, "marked_reupload": self.marked_reupload,
                "untouched": dict(sorted(self.untouched.items()))}


async def _scan_batch(conn, *, after: int, limit: int, workspace: str | None, profile: str):
    # keyset pagination: a server-side LIMIT over the ordered scan
    cur = await conn.execute(_SCAN_SQL + " limit %(limit)s",
                             {"after": after, "limit": limit, "workspace": workspace, "profile": profile})
    return await cur.fetchall()


async def run_backfill(
    conn: psycopg.AsyncConnection, *, batch_size: int = 50, dry_run: bool = False, workspace: str | None = None,
    after_id: int = 0, max_sources: int | None = None, requeue_failed: bool = False,
    profile: str = INDEX_PROFILE_NAME, on_batch: Callable[[BackfillReport], Awaitable[None] | None] | None = None,
) -> BackfillReport:
    """Queue structural-v1 builds. `conn` must be an autocommit connection (each batch opens its own transaction)."""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    report = BackfillReport(dry_run=dry_run, last_id=after_id)
    cursor = after_id
    while max_sources is None or report.scanned < max_sources:
        limit = batch_size if max_sources is None else min(batch_size, max_sources - report.scanned)
        rows = await _scan_batch(conn, after=cursor, limit=limit, workspace=workspace, profile=profile)
        if not rows:
            break
        async with conn.transaction():
            for fid, _wid, ver_id, _reason, job_id, err_code, state in rows:
                if state == "pending":
                    if not dry_run:
                        cur = await conn.execute(_INSERT_JOB, (fid, ver_id))
                        if cur.rowcount == 0:  # lost a race with another run: it queued it
                            report.untouched["building"] = report.untouched.get("building", 0) + 1
                            continue
                    report.queued += 1
                elif state == "no_blob":
                    if not dry_run:
                        await conn.execute(_MARK_REUPLOAD, ("NO_BLOB", fid))
                    report.marked_reupload += 1
                elif state == "failed" and err_code in REUPLOAD_ERROR_CODES:
                    if not dry_run:
                        await conn.execute(_MARK_REUPLOAD, (err_code, fid))
                    report.marked_reupload += 1
                elif (state == "failed" and requeue_failed) or state == "stalled":
                    if not dry_run:
                        await conn.execute(_RESET_JOB, (job_id,))
                    report.requeued += 1
                else:
                    report.untouched[state] = report.untouched.get(state, 0) + 1
        cursor = rows[-1][0]
        report.batches += 1
        report.scanned += len(rows)
        report.last_id = cursor
        if on_batch is not None:
            res = on_batch(report)
            if asyncio.iscoroutine(res):
                await res
        if len(rows) < limit:
            break
    return report


async def progress(conn: psycopg.AsyncConnection, *, workspace: str | None = None,
                   profile: str = INDEX_PROFILE_NAME) -> dict:
    """Admin-visible progress: how many active Sources are in each upgrade state."""
    cur = await conn.execute(
        f"select state, count(*) from ({_SCAN_SQL}) s group by state",
        {"after": 0, "workspace": workspace, "profile": profile})
    counts = {s: 0 for s in STATES}
    counts.update({state: n for state, n in await cur.fetchall()})
    total = sum(counts.values())
    cur = await conn.execute(
        "select coalesce(f.reupload_reason, 'UNKNOWN'), count(*) from files f join workspaces w on w.id = f.workspace_id "
        "where f.deleted_at is null and f.reupload_required_at is not null and w.kind <> 'eval' "
        "and (%(w)s::uuid is null or f.workspace_id = %(w)s::uuid) group by 1", {"w": workspace})
    reasons = {r: n for r, n in await cur.fetchall()}
    done = counts["upgraded"] + counts["reupload"]
    return {"total": total, "states": counts, "reupload_reasons": reasons,
            "upgraded_pct": round(100 * counts["upgraded"] / total, 1) if total else 100.0,
            "retire_ready": total == done, "waiting_on": total - done}


# ------------------------------------------------------------------ CLI

def _dsn(arg: str | None) -> str:
    if arg:
        return arg
    from scribe_intel.config import get_settings

    dsn = get_settings().dsn
    if not dsn:
        raise SystemExit("no database: pass --database-url or set DATABASE_URL / NEON_DATABASE_URL")
    return dsn


async def _amain(args: argparse.Namespace) -> int:
    async with await psycopg.AsyncConnection.connect(_dsn(args.database_url), autocommit=True,
                                                     prepare_threshold=None) as conn:
        if args.command == "progress":
            out = await progress(conn, workspace=args.workspace)
            print(json.dumps(out, indent=2) if args.json else _fmt_progress(out))
            return 0

        def log(r: BackfillReport) -> None:
            if not args.json:
                print(f"batch {r.batches}: scanned {r.scanned} (resume with --after-id {r.last_id}) "
                      f"queued {r.queued} requeued {r.requeued} marked_reupload {r.marked_reupload}", file=sys.stderr)

        rep = await run_backfill(conn, batch_size=args.batch_size, dry_run=args.dry_run, workspace=args.workspace,
                                 after_id=args.after_id, max_sources=args.max_sources,
                                 requeue_failed=args.requeue_failed, on_batch=log)
        label = "DRY RUN (nothing written)" if rep.dry_run else "done"
        print(json.dumps(rep.as_dict(), indent=2) if args.json else
              f"{label}: scanned {rep.scanned}; queued {rep.queued}; requeued {rep.requeued}; "
              f"marked re-upload {rep.marked_reupload}; untouched {dict(sorted(rep.untouched.items()))}")
        return 0


def _fmt_progress(p: dict) -> str:
    lines = [f"active sources: {p['total']}  upgraded: {p['upgraded_pct']}%  retire-ready: {p['retire_ready']}"]
    lines += [f"  {s:<13}{n}" for s, n in p["states"].items()]
    if p["reupload_reasons"]:
        lines.append(f"  re-upload reasons: {p['reupload_reasons']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scribe_ingest.upgrade", description=__doc__.split("\n\n")[0])
    ap.add_argument("--database-url", default=None, help="privileged DSN (default: DATABASE_URL / NEON_DATABASE_URL)")
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backfill", help="queue structural-v1 builds for legacy Sources")
    b.add_argument("--dry-run", action="store_true", help="only report counts; write nothing")
    b.add_argument("--batch-size", type=int, default=50)
    b.add_argument("--after-id", type=int, default=0, help="resume the scan after this source id")
    b.add_argument("--max-sources", type=int, default=None, help="stop after scanning this many sources")
    b.add_argument("--requeue-failed", action="store_true", help="give failed (transient) upgrade jobs a fresh budget")
    b.add_argument("--workspace", default=None, help="only this workspace id")
    b.add_argument("--json", action="store_true")
    p = sub.add_parser("progress", help="upgrade progress per state")
    p.add_argument("--workspace", default=None)
    p.add_argument("--json", action="store_true")
    return asyncio.run(_amain(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())

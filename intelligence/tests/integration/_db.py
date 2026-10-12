from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "fixtures" / "corpus"
GOLDEN = REPO / "benchmarks" / "datasets" / "golden-v1" / "cases.jsonl"


# Workspaces that outlive a single test module on purpose (process-wide caches such as the T13 eval corpus).
# The module-level purge skips them; the session-level purge removes everything created in the run.
PROTECTED_WORKSPACES: set[str] = set()

# Delete order for workspace-scoped rows. `files` cascades to source_versions / index_generations /
# active_indexes / documents / ingestion_jobs; the explicit leftovers pass catches anything inserted
# without a file. Seeded profile tables (retrieval/embedding/index/chunk profiles) are never touched.
_PURGE_FIRST = ("source_version_ocr_pages", "retrieval_traces", "retrieval_candidates", "evidence_items", "evidence_bundles", "rag_traces",
                "claim_verifications", "chats", "chunk_source_spans", "chunk_embeddings_1536",
                "chunk_embeddings_3072", "chunks", "files")
_PURGE_LEFTOVERS = ("active_indexes", "document_blocks", "index_generations", "source_versions",
                    "workspace_members")


def workspace_ids(url: str) -> set[str]:
    with psycopg.connect(url, autocommit=True) as c:
        return {str(r[0]) for r in c.execute("select id from workspaces").fetchall()}


def purge_workspaces(url: str, wids) -> None:
    """Delete every row belonging to the given workspaces (and the workspaces), in dependency order."""
    wids = sorted(set(wids))
    if not wids:
        return
    with psycopg.connect(url, autocommit=True) as c:
        for table in _PURGE_FIRST + _PURGE_LEFTOVERS:
            c.execute(f"delete from {table} where workspace_id = any(%s::uuid[])", (wids,))
        c.execute("delete from workspaces where id = any(%s::uuid[])", (wids,))


async def aconnect(url: str) -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(url, autocommit=True, prepare_threshold=None)


async def new_workspace(conn, kind: str = "eval") -> str:
    cur = await conn.execute("insert into workspaces(kind, owner_user_id) values (%s,%s) returning id",
                             (kind, f"t12-{uuid.uuid4()}"))
    return str((await cur.fetchone())[0])


async def new_file(conn, wid: str, name="note.md", mime="text/markdown", storage_url=None) -> int:
    cur = await conn.execute(
        "insert into files(name,type,size,user_id,workspace_id,status,storage_url) "
        "values (%s,%s,1,'t12',%s,'processing',%s) returning id", (name, mime, wid, storage_url))
    return (await cur.fetchone())[0]


def doc(n: int) -> bytes:
    body = "\n\n".join(f"Paragraph {i} of version {n}. " + "The quick brown fox jumps. " * 12 for i in range(6))
    return f"# Title {n}\n\n{body}\n\n## Second\n\nMore text for version {n}.\n".encode()


_FOREIGN_JOBS_MSG = (
    "foreign claimable ingestion jobs (queued/retrying/processing) are present in the shared test DB: leftovers from a "
    "killed run would be claimed by these tests. Purge them with "
    "`tests/integration/_db.py::purge_workspaces(url, workspace_ids(url))` (see purge_workspaces) on the throwaway test DB."
)


def fail_or_skip_foreign_jobs() -> None:
    """One policy for every suite that cannot run next to claimable jobs it does not own: FAIL when the CI env var is
    set (a skip there would silently disable the suite), otherwise skip locally and name the purge helper."""
    if os.environ.get("CI"):
        pytest.fail("CI: " + _FOREIGN_JOBS_MSG, pytrace=False)
    pytest.skip(_FOREIGN_JOBS_MSG)


async def skip_or_fail_if_foreign_jobs(conn) -> None:
    """Call first thing in a DB fixture; closes `conn` before skipping or failing."""
    cur = await conn.execute("select count(*) from ingestion_jobs where status in ('queued','retrying','processing')")
    if (await cur.fetchone())[0]:
        await conn.close()
        fail_or_skip_foreign_jobs()

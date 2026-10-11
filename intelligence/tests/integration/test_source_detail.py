"""T24: the Source detail page queries (lib/files/source-detail.ts) run against a real database.

The SQL is extracted from the TypeScript exactly as the app runs it (placeholders -> psycopg), then executed over data
produced by the real leased step machine with fake embeddings: blocks, parent/child chunks and spans come from the
indexer, versions from the real reserve/complete functions (migration 0015)."""
from __future__ import annotations

import re

import pytest
from _db import REPO, new_workspace
from conftest import needs_db
from scribe_ingest.embedding import FakeEmbedder
from scribe_intel.ingest_steps import run_step
from test_source_versions import V1, V2, V3, env, scalar, upload_version, v1_active  # noqa: F401  (env is a fixture)

pytestmark = needs_db
SRC = REPO / "lib" / "files" / "source-detail.ts"


def ts_sql(function: str) -> str:
    """The first sql`...` template after `export async function <function>`, with ${name} -> %(name)s."""
    ts = SRC.read_text()
    body = ts[ts.index(f"export async function {function}"):]
    body = body[body.index("sql`") + 4:]
    body = body[:body.index("`)")]
    named = re.sub(r"\$\{(\w+)\}", r"@@\1@@", body)
    return re.sub(r"@@(\w+)@@", r"%(\1)s", named.replace("%", "%%"))


async def rows(conn, function: str, wid, fid, *, user="t12", limit=50, offset=0, kind="all", version=None):
    params = {"workspaceId": wid, "userId": user, "sourceId": fid, "fetchLimit": limit + 1, "offset": offset,
              "kind": kind, "versionNumber": version}
    cur = await conn.execute(ts_sql(function), params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in await cur.fetchall()]


async def test_extractor_finds_every_query():
    for fn in ("getSourceHeader", "listStructure", "listChunks", "listIngestionHistory", "getOcrSummary", "resolveVersionBlob"):
        assert "FROM" in ts_sql(fn)


async def test_header_is_scoped_by_workspace_user_and_deleted_at(env):  # noqa: F811
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    (h,) = await rows(conn, "getSourceHeader", wid, fid)
    assert h["active_version_id"] == v1 and h["active_version_number"] == 1 and h["status"] == "ready"
    other = await new_workspace(conn)
    assert await rows(conn, "getSourceHeader", other, fid) == []  # another workspace: 404
    assert await rows(conn, "getSourceHeader", wid, fid, user="intruder") == []
    await conn.execute("update files set deleted_at = now() where id=%s", (fid,))
    for fn in ("getSourceHeader", "listStructure", "listChunks", "listIngestionHistory", "getOcrSummary", "resolveVersionBlob"):
        assert await rows(conn, fn, wid, fid, version=1) == [], fn  # soft-deleted: 404 everywhere


async def test_structure_is_paginated_in_document_order_with_heading_paths_and_ocr(env):  # noqa: F811
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    total = await scalar(conn, "select count(*) from document_blocks where source_version_id=%s", (v1,))
    assert total >= 4
    await conn.execute("update document_blocks set ocr = true, page = 2 where source_version_id=%s and block_index = 1", (v1,))
    first = await rows(conn, "listStructure", wid, fid, limit=2)
    assert [r["block_index"] for r in first] == [0, 1, 2]  # limit + 1 probe row
    assert first[1]["ocr"] is True and first[1]["page"] == 2
    second = await rows(conn, "listStructure", wid, fid, limit=2, offset=2)
    assert [r["block_index"] for r in second][0] == 2
    everything = await rows(conn, "listStructure", wid, fid, limit=200)
    assert len(everything) == total
    assert everything[0]["heading_path"] is not None and len(everything[0]["preview"]) <= 240
    assert await rows(conn, "listStructure", wid, fid, user="intruder") == []


async def test_chunks_are_the_active_generation_only_with_spans_tokens_and_kinds(env):  # noqa: F811
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    v1_chunks = await scalar(conn, "select count(*) from chunks where source_version_id=%s", (v1,))
    # v2 uploaded but not built: the Source still shows v1's generation
    v2, _, _ = await upload_version(conn, wid, fid, blobs, V2)
    allc = await rows(conn, "listChunks", wid, fid, limit=200)
    assert len(allc) == v1_chunks
    parents = await rows(conn, "listChunks", wid, fid, limit=200, kind="parent")
    children = await rows(conn, "listChunks", wid, fid, limit=200, kind="child")
    assert len(parents) + len(children) == v1_chunks and parents and children
    assert all(r["is_parent"] for r in parents) and not any(r["is_parent"] for r in children)
    assert all(r["child_count"] is not None and r["child_count"] >= 1 for r in parents)
    assert all(r["token_count"] and r["token_count"] > 0 for r in allc)
    assert all(r["parent_id"] is not None for r in children)
    spanned = [r for r in children if r["spans"]]
    assert spanned and all({"page", "charStart", "charEnd"} <= set(s) for r in spanned for s in r["spans"])
    assert [r["chunk_index"] for r in allc] == sorted(r["chunk_index"] for r in allc)
    # pagination bound
    page = await rows(conn, "listChunks", wid, fid, limit=3, offset=1)
    assert [r["chunk_index"] for r in page] == [r["chunk_index"] for r in allc[1:5]]

    # build and activate v2: v1's superseded chunks are still stored but must no longer be listed
    r = await run_step(conn, FakeEmbedder(), download=blobs)
    assert r["outcome"] == "completed"
    v2_chunks = await scalar(conn, "select count(*) from chunks where source_version_id=%s", (v2,))
    assert await scalar(conn, "select count(*) from chunks where source_version_id=%s", (v1,)) > 0
    now = await rows(conn, "listChunks", wid, fid, limit=200)
    assert len(now) == v2_chunks
    assert await scalar(conn, "select count(*) from chunks where id = any(%s) and source_version_id=%s",
                        ([r["id"] for r in now], v2)) == v2_chunks
    assert await rows(conn, "listChunks", wid, fid, user="intruder") == []
    assert await rows(conn, "listChunks", await new_workspace(conn), fid) == []


async def test_history_lists_jobs_of_every_version_newest_first_and_ocr_counts(env):  # noqa: F811
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    v2, _, _ = await upload_version(conn, wid, fid, blobs, V2)
    jobs = await rows(conn, "listIngestionHistory", wid, fid)
    assert len(jobs) == 2
    assert jobs[0]["source_version_id"] == v2 and jobs[0]["version_number"] == 2 and jobs[0]["status"] == "queued"
    assert jobs[1]["status"] == "completed" and jobs[1]["stage"] == "DONE" and jobs[1]["attempts"] >= 1
    assert len(await rows(conn, "listIngestionHistory", wid, fid, limit=1)) == 2  # one + probe
    assert await rows(conn, "listIngestionHistory", wid, fid, user="intruder") == []

    (ocr,) = await rows(conn, "getOcrSummary", wid, fid)
    assert (ocr["ocr_pages_done"], ocr["ocr_block_pages"], ocr["ocr_blocks"]) == (0, 0, 0)
    await conn.execute("update document_blocks set ocr = true, page = 3 where source_version_id=%s and block_index in (0, 1)", (v1,))
    await conn.execute("insert into source_version_ocr_pages(source_version_id, workspace_id, page, text, model) "
                       "values (%s,%s,3,'t','m'), (%s,%s,4,'','m')", (v1, wid, v1, wid))
    (ocr,) = await rows(conn, "getOcrSummary", wid, fid)
    assert (ocr["ocr_pages_done"], ocr["ocr_block_pages"], ocr["ocr_blocks"]) == (2, 1, 2)
    await conn.execute("delete from source_version_ocr_pages where source_version_id=%s", (v1,))


async def test_blob_resolution_follows_duplicate_of_and_the_legacy_source_url(env):  # noqa: F811
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)  # T18 flow: the blob URL is files.storage_url
    v2, _, _ = await upload_version(conn, wid, fid, blobs, V2)
    v3, n3, out = await upload_version(conn, wid, fid, blobs, V1)  # same bytes as v1
    assert out[0] == "duplicate" and n3 == 3
    dup_own_key = await scalar(conn, "select storage_key from source_versions where id=%s", (v3,))
    v2_key = await scalar(conn, "select storage_key from source_versions where id=%s", (v2,))
    storage_url = await scalar(conn, "select storage_url from files where id=%s", (fid,))

    (active,) = await rows(conn, "resolveVersionBlob", wid, fid, version=None)  # no version: the active one (v1)
    assert (active["version_number"], active["blob_version_number"]) == (1, 1) and active["storage_url"] == storage_url
    (second,) = await rows(conn, "resolveVersionBlob", wid, fid, version=2)
    assert second["blob_key"] == v2_key and second["upload_state"] == "stored"
    (dup,) = await rows(conn, "resolveVersionBlob", wid, fid, version=3)
    # the duplicate's own blob was deleted by the app; the bytes come from the version it duplicates
    assert dup["version_number"] == 3 and dup["blob_version_number"] == 1
    assert dup["blob_key"] != dup_own_key
    assert await rows(conn, "resolveVersionBlob", wid, fid, version=9) == []
    assert await rows(conn, "resolveVersionBlob", wid, fid, user="intruder", version=3) == []
    assert await rows(conn, "resolveVersionBlob", await new_workspace(conn), fid, version=3) == []

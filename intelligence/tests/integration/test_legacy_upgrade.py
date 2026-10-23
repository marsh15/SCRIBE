"""T22: backfill of legacy Sources onto structural-v1 (shared test DB, fake blob store, fake embedder)."""
from __future__ import annotations

import pytest
from psycopg.types.json import Jsonb
from _db import aconnect, doc, new_workspace, skip_or_fail_if_foreign_jobs
from _legacy import legacy_source
from conftest import DATABASE_URL_TEST, needs_db
from scribe_ingest import upgrade
from scribe_ingest.blob import BlobDownloadError
from scribe_ingest.embedding import FakeEmbedder
from scribe_ingest.indexer import IndexerError
from scribe_intel.ingest_steps import run_step

pytestmark = needs_db
BLOB = "https://blob.example/legacy.md"


class Killed(BaseException):
    """The process dies: not an Exception, so nothing in the code under test can handle it."""


@pytest.fixture()
async def env():
    conn = await aconnect(DATABASE_URL_TEST)
    await skip_or_fail_if_foreign_jobs(conn)
    wid = await new_workspace(conn, kind="personal")
    yield conn, wid
    await conn.execute("delete from usage_events where source_id in (select id::text from files where workspace_id=%s)", (wid,))
    await conn.execute("delete from ingestion_jobs where file_id in (select id from files where workspace_id=%s)", (wid,))
    await conn.close()  # workspace rows are purged by the module fixture


async def jobs(conn, wid):
    cur = await conn.execute("select j.file_id, j.status, j.source_version_id from ingestion_jobs j join files f on f.id=j.file_id "
                             "where f.workspace_id=%s order by j.file_id", (wid,))
    return await cur.fetchall()


async def flag(conn, fid):
    cur = await conn.execute("select reupload_required_at is not null, reupload_reason from files where id=%s", (fid,))
    return await cur.fetchone()


async def test_dry_run_reports_counts_and_writes_nothing(env):
    conn, wid = env
    a = await legacy_source(conn, wid, name="a.md")
    b = await legacy_source(conn, wid, name="b.md", storage_url=None)
    rep = await upgrade.run_backfill(conn, dry_run=True, workspace=wid)
    assert (rep.scanned, rep.queued, rep.marked_reupload, rep.dry_run) == (2, 1, 1, True)
    assert await jobs(conn, wid) == []
    assert await flag(conn, a) == (False, None) and await flag(conn, b) == (False, None)
    assert (await upgrade.progress(conn, workspace=wid))["states"]["pending"] == 1


async def test_backfill_queues_exactly_one_job_per_source_and_is_idempotent(env):
    conn, wid = env
    ids = [await legacy_source(conn, wid, name=f"s{i}.md") for i in range(3)]
    gone = await legacy_source(conn, wid, name="gone.md", storage_url="  ")  # blank url counts as missing
    rep = await upgrade.run_backfill(conn, batch_size=2, workspace=wid)
    assert (rep.scanned, rep.queued, rep.marked_reupload, rep.batches) == (4, 3, 1, 2)
    first = await jobs(conn, wid)
    assert [j[0] for j in first] == ids and {j[1] for j in first} == {"queued"}
    assert all(j[2] is not None for j in first)  # bound to version 1, not "any version of the file"
    again = await upgrade.run_backfill(conn, batch_size=2, workspace=wid)
    assert (again.queued, again.requeued, again.marked_reupload) == (0, 0, 0)
    assert again.untouched == {"building": 3, "reupload": 1}
    assert await jobs(conn, wid) == first
    assert await flag(conn, gone) == (True, "NO_BLOB")


async def test_missing_blob_keeps_legacy_active_and_is_visible(env):
    conn, wid = env
    fid = await legacy_source(conn, wid, storage_url=None)
    await upgrade.run_backfill(conn, workspace=wid)
    assert await flag(conn, fid) == (True, "NO_BLOB")
    p = await upgrade.progress(conn, workspace=wid)
    assert p["states"]["reupload"] == 1 and p["reupload_reasons"] == {"NO_BLOB": 1} and p["retire_ready"] is True
    cur = await conn.execute(
        "select g.status, (select count(*) from chunk_embeddings_3072 e join chunks c on c.id=e.chunk_id where c.generation_id=g.id) "
        "from index_generations g join index_profiles ip on ip.id=g.index_profile_id "
        "where g.source_id=%s and ip.name='legacy-v1'", (fid,))
    assert await cur.fetchone() == ("ACTIVE", 1)  # still searchable through legacy-dense
    assert await jobs(conn, wid) == []


async def test_queue_is_drained_by_the_existing_step_machine_on_version_1(env):
    conn, wid = env
    fid = await legacy_source(conn, wid)
    await upgrade.run_backfill(conn, workspace=wid)
    r = await run_step(conn, FakeEmbedder(), download=lambda url, tok, **k: doc(1))
    assert r["outcome"] == "completed", r
    cur = await conn.execute(
        "select ip.name, g.status, v.version_number from index_generations g join index_profiles ip on ip.id=g.index_profile_id "
        "join source_versions v on v.id=g.source_version_id where g.source_id=%s order by ip.name", (fid,))
    assert await cur.fetchall() == [("legacy-v1", "ACTIVE", 1), ("structural-v1", "ACTIVE", 1)]  # no spurious version 2
    cur = await conn.execute("select count(*) from source_versions where source_id=%s", (fid,))
    assert (await cur.fetchone())[0] == 1
    p = await upgrade.progress(conn, workspace=wid)
    assert p["states"]["upgraded"] == 1 and p["retire_ready"] is True
    rep = await upgrade.run_backfill(conn, workspace=wid)
    assert (rep.queued, rep.requeued, rep.marked_reupload) == (0, 0, 0) and rep.untouched == {"upgraded": 1}


async def _seed_structural_alias(conn, name="structural-test-alias"):
    row = await (await conn.execute(
        "select parser_profile, chunk_profile_id, embedding_profile_id, config from index_profiles "
        "where name='structural-v1'" )).fetchone()
    cur = await conn.execute(
        "insert into index_profiles(name, parser_profile, chunk_profile_id, embedding_profile_id, config) "
        "values (%s,%s,%s,%s,%s) on conflict do nothing returning id",
        (name, row[0], row[1], row[2], Jsonb(row[3])))
    inserted = await cur.fetchone()
    if inserted:
        return name, inserted[0]
    cur = await conn.execute("select id from index_profiles where name=%s", (name,))
    return name, (await cur.fetchone())[0]


async def test_backfill_target_profile_is_carried_through_generation_and_worker(env):
    conn, wid = env
    fid = await legacy_source(conn, wid)
    alias, alias_id = await _seed_structural_alias(conn)

    rep = await upgrade.run_backfill(conn, workspace=wid, profile=alias)
    assert rep.queued == 1
    cur = await conn.execute(
        "select j.generation_id, g.index_profile_id from ingestion_jobs j "
        "join index_generations g on g.id=j.generation_id where j.file_id=%s", (fid,))
    generation_id, profile_id = await cur.fetchone()
    assert profile_id == alias_id

    result = await run_step(conn, FakeEmbedder(), download=lambda url, tok, **k: doc(1))
    assert result["outcome"] == "completed", result
    cur = await conn.execute(
        "select ip.name, g.status, g.expected_chunks, g.embedded_chunks from index_generations g "
        "join index_profiles ip on ip.id=g.index_profile_id where g.id=%s", (generation_id,))
    assert await cur.fetchone() == (alias, "ACTIVE", 3, 3)
    progress = await upgrade.progress(conn, workspace=wid, profile=alias)
    assert progress["sources"][0]["stage"] == "DONE"
    assert progress["sources"][0]["embedded_chunks"] == progress["sources"][0]["expected_chunks"] == 3
    assert progress["totals"] == {"sources": 1, "embedded_chunks": 3, "expected_chunks": 3,
                                  "progress_pct": 100.0, "failures": 0}
    again = await upgrade.run_backfill(conn, workspace=wid, profile=alias)
    assert again.queued == 0 and again.untouched == {"upgraded": 1}
    assert (await jobs(conn, wid))[0][1] == "completed"


async def test_progress_has_per_source_stage_and_does_not_invent_zero_totals(env):
    conn, wid = env
    fid = await legacy_source(conn, wid)
    rep = await upgrade.run_backfill(conn, workspace=wid)
    assert rep.queued == 1
    progress = await upgrade.progress(conn, workspace=wid)
    assert progress["total"] == 1  # existing aggregate field remains available
    assert progress["sources"] == [{
        "source_id": fid, "name": "legacy.md", "state": "building", "stage": "BUILDING",
        "generation_id": progress["sources"][0]["generation_id"], "embedded_chunks": 0,
        "expected_chunks": 0, "job_id": progress["sources"][0]["job_id"],
        "job_status": "queued", "error_code": None, "reupload_reason": None,
    }]
    assert progress["totals"]["progress_pct"] is None
    assert progress["totals"]["expected_chunks"] == 0


async def test_legacy_and_unknown_profiles_are_rejected_before_backfill_writes(env):
    conn, wid = env
    fid = await legacy_source(conn, wid)
    for profile, phrase in (("legacy-v1", "not supported for backfill"),
                            ("missing-profile", "not found")):
        with pytest.raises(IndexerError, match=phrase):
            await upgrade.run_backfill(conn, workspace=wid, profile=profile)
        with pytest.raises(IndexerError, match=phrase):
            await upgrade.progress(conn, workspace=wid, profile=profile)
    assert await jobs(conn, wid) == []
    cur = await conn.execute("select count(*) from index_generations where source_id=%s", (fid,))
    assert (await cur.fetchone())[0] == 1  # the pre-existing legacy generation only


async def test_unreadable_blob_fails_permanently_and_is_marked_reupload(env):
    conn, wid = env
    fid = await legacy_source(conn, wid)
    await upgrade.run_backfill(conn, workspace=wid)

    def gone(url, tok, **k):
        raise BlobDownloadError("Blob download failed with HTTP 404.", status=404, retryable=False)

    r = await run_step(conn, FakeEmbedder(), download=gone)
    assert r["outcome"] == "failed" and r["error_code"] == "INVALID_DOCUMENT"
    assert (await upgrade.progress(conn, workspace=wid))["states"]["failed"] == 1
    rep = await upgrade.run_backfill(conn, workspace=wid)
    assert rep.marked_reupload == 1 and rep.queued == 0
    assert await flag(conn, fid) == (True, "INVALID_DOCUMENT")
    cur = await conn.execute("select status from files where id=%s", (fid,))
    assert (await cur.fetchone())[0] == "ready"  # the Source never stops working
    cur = await conn.execute("select count(*) from chunk_embeddings_3072 e join chunks c on c.id=e.chunk_id "
                             "where c.source_version_id=(select active_version_id from files where id=%s)", (fid,))
    assert (await cur.fetchone())[0] == 1
    # once it is re-queued by hand and succeeds, the mark is cleared by the activation
    await conn.execute("update ingestion_jobs set status='queued', stage=null, attempts=0, error_code=null, finished_at=null "
                       "where file_id=%s", (fid,))
    assert (await run_step(conn, FakeEmbedder(), download=lambda *a, **k: doc(1)))["outcome"] == "completed"
    assert await flag(conn, fid) == (False, None)


async def test_transient_failures_wait_for_requeue_failed(env):
    conn, wid = env
    fid = await legacy_source(conn, wid)
    await upgrade.run_backfill(conn, workspace=wid)
    await conn.execute("update ingestion_jobs set status='failed', attempts=5, error_code='RATE_LIMITED' where file_id=%s", (fid,))
    rep = await upgrade.run_backfill(conn, workspace=wid)
    assert rep.requeued == 0 and rep.untouched == {"failed": 1}
    rep = await upgrade.run_backfill(conn, workspace=wid, requeue_failed=True)
    assert rep.requeued == 1
    assert (await jobs(conn, wid))[0][1] == "queued"


async def test_resumable_after_a_killed_run(env):
    conn, wid = env
    ids = [await legacy_source(conn, wid, name=f"k{i}.md") for i in range(5)]

    def die_after_second_batch(rep):
        if rep.batches == 2:
            raise Killed()

    with pytest.raises(Killed):
        await upgrade.run_backfill(conn, batch_size=2, workspace=wid, on_batch=die_after_second_batch)
    assert len(await jobs(conn, wid)) == 4  # committed batches survive

    class DiesInsideBatch:  # the third INSERT of a fresh run dies mid-transaction: that batch rolls back whole
        def __init__(self, c):
            self.c, self.n = c, 0

        def transaction(self):
            return self.c.transaction()

        async def execute(self, sql, *a, **k):
            if sql.startswith("insert into ingestion_jobs"):
                self.n += 1
                if self.n == 2:
                    raise Killed()
            return await self.c.execute(sql, *a, **k)

    await conn.execute("delete from ingestion_jobs where file_id in (select id from files where workspace_id=%s)", (wid,))
    with pytest.raises(Killed):
        await upgrade.run_backfill(DiesInsideBatch(conn), batch_size=3, workspace=wid)
    assert await jobs(conn, wid) == []  # nothing half-queued

    rep = await upgrade.run_backfill(conn, batch_size=2, workspace=wid)
    assert rep.queued == 5
    assert [j[0] for j in await jobs(conn, wid)] == ids  # exactly once each, no duplicates
    assert (await upgrade.run_backfill(conn, batch_size=2, workspace=wid)).queued == 0


async def test_resume_from_after_id_and_max_sources(env):
    conn, wid = env
    ids = [await legacy_source(conn, wid, name=f"r{i}.md") for i in range(4)]
    rep = await upgrade.run_backfill(conn, batch_size=2, workspace=wid, max_sources=2)
    assert (rep.scanned, rep.queued, rep.last_id) == (2, 2, ids[1])
    rep = await upgrade.run_backfill(conn, batch_size=2, workspace=wid, after_id=rep.last_id)
    assert (rep.scanned, rep.queued) == (2, 2)
    assert len(await jobs(conn, wid)) == 4


async def test_skips_eval_workspaces_deleted_sources_and_in_flight_uploads(env):
    conn, wid = env
    ev = await new_workspace(conn, kind="eval")
    await legacy_source(conn, ev)
    deleted = await legacy_source(conn, wid, name="d.md")
    await conn.execute("update files set deleted_at=now() where id=%s", (deleted,))
    newer = await legacy_source(conn, wid, name="n.md")
    await conn.execute("update files set desired_version_id=NULL where id=%s", (newer,))  # a newer upload is pending
    assert (await upgrade.run_backfill(conn, workspace=ev)).scanned == 0
    rep = await upgrade.run_backfill(conn, workspace=wid)
    assert (rep.scanned, rep.queued, rep.untouched) == (1, 0, {"newer_upload": 1})
    assert await jobs(conn, wid) == []

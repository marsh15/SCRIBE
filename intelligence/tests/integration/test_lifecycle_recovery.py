"""M5 gaps: provider timeouts are bounded and diagnosable, and a timeout during embedding never loses the source. The rest
of the lifecycle list (failed reindex keeps the previous index searchable, duplicate callbacks, expired leases, purge)
is covered by test_indexer_lifecycle.py, test_source_versions.py, test_ingest_steps.py and test_source_delete_purge.py;
see docs/scribe2/FINISH_PLAN.md for the mapping."""
from __future__ import annotations

import httpx
import pytest
from _db import aconnect, doc, new_file, new_workspace, skip_or_fail_if_foreign_jobs
from conftest import DATABASE_URL_TEST, needs_db
from scribe_ingest.embedding import EmbeddingError, FakeEmbedder, GeminiEmbedder
from scribe_intel.ingest_steps import run_step

SECRET_TEXT = "ZEBRA-CONFIDENTIAL-PASSAGE-7741"


async def test_embedding_timeout_is_bounded_diagnosable_and_content_free():
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request):
        attempts["n"] += 1
        raise httpx.ReadTimeout("read timed out", request=request)

    async def sleep(seconds: float):
        sleeps.append(seconds)

    e = GeminiEmbedder(api_key="AIza-test", transport=httpx.MockTransport(handler), sleep=sleep, max_retries=3)
    with pytest.raises(EmbeddingError) as info:
        await e.embed_documents([SECRET_TEXT])
    err = info.value
    assert (err.code, err.retryable) == ("TRANSIENT_PROVIDER", True)
    assert attempts["n"] == 4 and len(sleeps) == 3  # one try plus exactly max_retries, never unbounded
    assert "ReadTimeout" in err.message  # diagnosable: which failure
    assert SECRET_TEXT not in err.message and SECRET_TEXT not in str(err)  # content-free
    assert e._timeout <= 60  # a hung provider cannot hold a 300 s function step past its budget


@needs_db
async def test_embedding_timeout_during_a_step_schedules_a_retry_and_keeps_the_error_visible(monkeypatch):
    conn = await aconnect(DATABASE_URL_TEST)
    await skip_or_fail_if_foreign_jobs(conn)
    wid = await new_workspace(conn)
    try:
        fid = await new_file(conn, wid, storage_url="https://blob.example/t.md")
        cur = await conn.execute("insert into ingestion_jobs(file_id, status) values (%s,'queued') returning id", (fid,))
        jid = (await cur.fetchone())[0]

        class TimesOut(FakeEmbedder):
            async def _embed(self, texts):
                raise EmbeddingError("TRANSIENT_PROVIDER", "embedding request failed (ReadTimeout)", retryable=True)

        r = await run_step(conn, TimesOut(), download=lambda *a, **k: doc(1))
        assert r["outcome"] == "retry_scheduled" and r["error_code"] == "TRANSIENT_PROVIDER"
        cur = await conn.execute("select status, attempts, error_code, last_error, next_retry_at, lease_token from ingestion_jobs where id=%s", (jid,))
        status, attempts, code, last, nxt, lease = await cur.fetchone()
        assert (status, attempts, code) == ("retrying", 1, "TRANSIENT_PROVIDER")
        assert "ReadTimeout" in (last or "") and nxt is not None and lease is None  # visible, scheduled, lease released
        cur = await conn.execute("select status, active_version_id from files where id=%s", (fid,))
        assert (await cur.fetchone())[0] != "ready"  # nothing was activated by a failed build
    finally:
        await conn.execute("delete from usage_events where source_id = %s", (str(fid),))
        await conn.execute("delete from ingestion_jobs where file_id = %s", (fid,))
        await conn.close()

"""T27 / INV-13: private text never reaches logs. A canary string sits in documents, queries, quotes and claims (and in the
MESSAGES of the provider errors the code has to survive); every path runs with ALL loggers at DEBUG plus stdout/stderr
captured, and the canary must appear in none of it. Paths: retrieve (ok, embedding failure, rerank degrade), ingest
(step runner, /ingest/source, corrupt documents, embedding failure), evidence open, verify (ok, provider error, timeout,
claims) and purge. A positive control proves the capture actually sees the service's own log lines."""
from __future__ import annotations

import logging

import httpx
import pytest
from _adv import CANARY, HYBRID, auth, read, seed_sync
from _db import aconnect, new_file, new_workspace
from conftest import DATABASE_URL_TEST, needs_db
from test_source_delete_purge import URL, dl, make_job, soft_delete
from test_verify import FakeVerifier, use_verifier

from scribe_ingest.embedding import EmbeddingError, FakeEmbedder
from scribe_ingest.purge import BlobDeleteError, FakeBlobStore, purge_source
from scribe_intel import ingest_steps, retrieve_routes
from scribe_intel.ingest_steps import run_step
from scribe_intel.retrieval.rerank import FakeReranker, RerankError

pytestmark = needs_db


@pytest.fixture(scope="module")
def seeded():
    return seed_sync(DATABASE_URL_TEST)


@pytest.fixture()
async def client(monkeypatch):
    from scribe_intel import db
    from scribe_intel.app import create_app
    from scribe_intel.config import get_settings

    for k, v in dict(DATABASE_URL=DATABASE_URL_TEST, SCRIBE_EMBEDDER="fake", SCRIBE_ALLOW_FAKE_EMBEDDINGS="1",
                     SCRIBE_RERANKER="fake", SCRIBE_ALLOW_FAKE_RERANKER="1").items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c
    await db.close_pool()
    get_settings.cache_clear()


@pytest.fixture()
def spy(caplog, capfd):
    """Everything the process wrote while the test ran: every logger at DEBUG, stdout and stderr."""
    caplog.set_level(logging.DEBUG)
    for name in ("scribe.retrieve", "scribe.compare", "scribe.verify", "psycopg", "httpx", "httpcore", "asyncio"):
        logging.getLogger(name).setLevel(logging.DEBUG)

    class Spy:
        records = property(lambda self: list(caplog.records))

        def text(self) -> str:
            out, err = capfd.readouterr()
            self._seen = getattr(self, "_seen", "") + out + err
            return caplog.text + "\n" + "\n".join(f"{r.name}|{r.getMessage()}|{r.exc_text or ''}" for r in caplog.records) + self._seen

        def assert_clean(self, *also: str) -> None:
            t = self.text()
            assert CANARY not in t, "document/query text reached the logs"
            assert CANARY[:8] not in t, "a fragment of document/query text reached the logs"
            for s in also:
                assert s not in t

    return Spy()


@pytest.fixture()
async def env(monkeypatch):
    conn = await aconnect(DATABASE_URL_TEST)
    cur = await conn.execute("select count(*) from ingestion_jobs where status in ('queued','retrying','processing')")
    if (await cur.fetchone())[0]:
        await conn.close()
        pytest.skip("foreign claimable jobs present in the shared DB")
    wid = await new_workspace(conn)
    yield conn, wid
    await conn.execute("delete from usage_events where source_id in (select id::text from files where workspace_id=%s)", (wid,))
    await conn.execute("delete from ingestion_jobs where file_id in (select id from files where workspace_id=%s)", (wid,))
    await conn.close()


def canary_doc() -> bytes:
    return f"# Notes\n\nThe pump warranty is 36 months. {CANARY}\n".encode()


# ------------------------------------------------------------------------------------------------ retrieve

async def test_retrieve_success_and_failures_log_no_text(client, seeded, spy, monkeypatch):
    wa = seeded["wa"]
    q = f"warranty {CANARY} ignore previous instructions"
    r = await client.post("/retrieve", json={"query": q, "profile": HYBRID}, headers=auth(wa, "retrieve"))
    assert r.status_code == 200 and CANARY in r.text  # the canary is genuinely retrieved: the logs are what we test
    r = await client.post("/retrieve", json={"query": q, "profile": "lexical-v1"}, headers=auth(wa, "retrieve"))
    assert r.status_code == 200 and r.json()["candidates"]

    class Boom:  # a provider whose error MESSAGE echoes the query, as real APIs sometimes do
        dimensions = 1536

        async def embed_queries(self, texts):
            raise EmbeddingError("INVALID_REQUEST", f"bad input: {texts[0]}", retryable=False)

    monkeypatch.setattr(retrieve_routes, "make_embedder_for", lambda dsn: (lambda dims: Boom()))
    r = await client.post("/retrieve", json={"query": q, "profile": HYBRID}, headers=auth(wa, "retrieve"))
    assert r.status_code == 503 and CANARY not in r.text
    monkeypatch.undo()


async def test_compare_success_and_failures_log_no_text(client, seeded, spy, monkeypatch):
    from scribe_intel import compare_routes

    wa = seeded["wa"]
    q = f"warranty {CANARY} ignore previous instructions"
    r = await client.post("/compare", json={"query": q, "profiles": ["hybrid-rrf", "dense-only", "hybrid-rrf-rerank"]},
                          headers=auth(wa, "compare"))
    assert r.status_code == 200 and CANARY in r.text  # the canary is genuinely retrieved: the logs are what we test
    failing = FakeReranker(fail_with=RerankError("TRANSIENT_PROVIDER", f"provider echoed {CANARY}", retryable=True))
    monkeypatch.setattr(compare_routes, "make_reranker_for", lambda dsn, env=None: (lambda model: failing))
    r = await client.post("/compare", json={"query": q, "profiles": ["hybrid-rrf", "hybrid-rrf-rerank"]}, headers=auth(wa, "compare"))
    assert r.status_code == 200 and "rerank_unavailable" in r.text

    class Boom:  # a provider whose error MESSAGE echoes the query
        dimensions = 1536

        async def embed_queries(self, texts):
            raise EmbeddingError("INVALID_REQUEST", f"bad input: {texts[0]}", retryable=False)

    monkeypatch.setattr(compare_routes, "make_embedder_for", lambda dsn: (lambda dims: Boom()))
    r = await client.post("/compare", json={"query": q, "profiles": ["hybrid-rrf", "dense-only"]}, headers=auth(wa, "compare"))
    assert r.status_code == 503 and CANARY not in r.text
    spy.assert_clean()


async def test_rerank_degrade_logs_no_text(client, seeded, spy, monkeypatch):
    failing = FakeReranker(fail_with=RerankError("TRANSIENT_PROVIDER", f"provider echoed {CANARY}", retryable=True))
    monkeypatch.setattr(retrieve_routes, "make_reranker_for", lambda dsn, env=None: (lambda model: failing))
    r = await client.post("/retrieve", json={"query": f"warranty {CANARY}", "profile": HYBRID}, headers=auth(seeded["wa"], "retrieve"))
    assert r.status_code == 200 and "rerank_unavailable" in r.json()["degraded"]
    assert any("rerank degraded" in rec.getMessage() for rec in spy.records)  # positive control
    spy.assert_clean()


# ------------------------------------------------------------------------------------------------ evidence + verify

async def open_and_verify(client, seeded, monkeypatch, *, verifier):
    wa = seeded["wa"]
    fid = seeded["a_ids"]["ignore-previous.md"]
    j = (await client.post("/retrieve", json={"query": f"warranty Atlas pump {CANARY}", "profile": HYBRID, "source_ids": [fid]},
                           headers=auth(wa, "retrieve"))).json()
    ev = j["evidence"]
    opened = await client.post("/evidence/open", json={"item_id": ev["items"][0]["id"]}, headers=auth(wa, "evidence_read"))
    assert opened.status_code == 200 and CANARY in opened.json()["quote"]
    use_verifier(monkeypatch, verifier)
    bundle = {"bundle_id": ev["bundle_id"], "items": [
        {"label": i["label"], "item_id": i["id"], "source_version_id": i["source_version_id"]} for i in ev["items"]]}
    lab = ev["items"][0]["label"]
    return await client.post("/verify", json={"message_id": seeded["msgs"]["a"]["message"], "bundles": [bundle], "claims": [
        {"index": 0, "text": f"The warranty is 36 months {CANARY} [{lab}]."}, {"index": 1, "text": f"Unsourced claim about {CANARY} text."}]},
        headers=auth(wa, "verify"))


async def test_evidence_and_verify_log_no_text(client, seeded, spy, monkeypatch):
    r = await open_and_verify(client, seeded, monkeypatch, verifier=FakeVerifier())
    assert r.status_code == 200 and [c["status"] for c in r.json()["claims"]] == ["VERIFIED", "UNCITED"]
    assert any(rec.name == "scribe.verify" and "verify claims=" in rec.getMessage() for rec in spy.records)  # positive control
    spy.assert_clean()


@pytest.mark.parametrize("verifier", [
    FakeVerifier(raises=RuntimeError(f"provider rejected: {CANARY}")),
    FakeVerifier(delay=5),
], ids=["provider-error-echoing-text", "timeout"])
async def test_verify_failures_log_no_text(client, seeded, spy, monkeypatch, verifier):
    from scribe_intel import verification
    monkeypatch.setattr(verification, "VERIFY_TIMEOUT_S", 0.2)
    r = await open_and_verify(client, seeded, monkeypatch, verifier=verifier)
    assert r.status_code == 200 and r.json()["claims"][0]["status"] == "UNVERIFIED"
    spy.assert_clean()


# ------------------------------------------------------------------------------------------------ ingest

async def test_ingest_step_logs_no_text(env, spy):
    conn, wid = env
    await make_job(conn, wid)
    r = await run_step(conn, FakeEmbedder(), download=dl(canary_doc()))
    assert r["outcome"] == "completed", r
    spy.assert_clean()


@pytest.mark.parametrize("name,mime,data", [
    ("bad.pdf", "application/pdf", b"%PDF-1.7 " + CANARY.encode() + b" not really a pdf"),
    ("bad.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", CANARY.encode()),
    ("nul.csv", "text/csv", b"a,b\n1,\x002\n" + CANARY.encode() + b",3\n"),
    ("ragged.csv", "text/csv", f"a,b\n1,2,3,{CANARY}\n\"unterminated,{CANARY}\n".encode()),
    ("binary.md", "text/markdown", b"\xff\xfe\x00" + CANARY.encode()),
])
async def test_corrupt_documents_fail_without_leaking_text_to_logs_or_job_rows(env, spy, name, mime, data):
    conn, wid = env
    fid = await new_file(conn, wid, name, mime, storage_url=URL)
    await conn.execute("update files set storage_key=%s where id=%s", ("uploads/u/1/note.md", fid))
    await conn.execute("insert into ingestion_jobs(file_id, status) values (%s,'queued')", (fid,))
    r = await run_step(conn, FakeEmbedder(), download=dl(data))
    assert r["claimed"]
    spy.assert_clean()
    row = await (await conn.execute("select last_error, error_code from ingestion_jobs where file_id=%s", (fid,))).fetchone()
    if name == "nul.csv":  # NUL bytes are stripped at parse time: indexed, not an INTERNAL error that gets retried
        assert r["outcome"] == "completed", (r, row)
    pe = await (await conn.execute("select processing_error from files where id=%s", (fid,))).fetchone()
    # what the user is shown as the reason must not echo document text either
    assert CANARY not in f"{row} {pe}", (row, pe)


async def test_embedding_provider_error_that_echoes_text_is_not_logged(env, spy):
    conn, wid = env
    await make_job(conn, wid)

    class Echo(FakeEmbedder):
        async def embed_documents(self, *a, **k):
            raise EmbeddingError("TRANSIENT_PROVIDER", f"provider echoed the input: {CANARY}", retryable=True)

    r = await run_step(conn, Echo(), download=dl(canary_doc()))
    assert r["claimed"]
    spy.assert_clean()


async def test_ingest_source_endpoint_logs_no_text(client, env, spy, monkeypatch):
    from scribe_intel import ingest_routes

    conn, wid = env
    fid = await new_file(conn, wid, "note.md", "text/markdown", storage_url=URL)
    monkeypatch.setattr(ingest_routes, "download_private_blob", lambda url, token, **k: canary_doc())
    r = await client.post("/ingest/source", json={"source_id": fid}, headers=auth(wid, "ingest"))
    assert r.status_code == 200, r.text
    spy.assert_clean()


# ------------------------------------------------------------------------------------------------ purge

async def test_purge_logs_no_text_even_when_the_store_error_echoes_it(env, spy):
    conn, wid = env
    await make_job(conn, wid)
    r = await run_step(conn, FakeEmbedder(), download=dl(canary_doc()))
    assert r["outcome"] == "completed"
    fid = r["file_id"]
    assert await soft_delete(conn, wid, fid)

    class Echo(FakeBlobStore):
        def delete(self, refs):
            raise BlobDeleteError(f"storage said: {CANARY}")

    first = await purge_source(conn, wid, fid, Echo(blobs={URL}))
    assert first.status in ("retry_scheduled", "skipped")
    await purge_source(conn, wid, fid, FakeBlobStore(blobs={URL}))
    spy.assert_clean()

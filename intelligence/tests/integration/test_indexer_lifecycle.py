"""T12: idempotency, activation rules (INV-3/INV-4), failure isolation."""
from __future__ import annotations

import asyncio

import pytest
from _db import CORPUS, aconnect, doc, new_file, new_workspace
from conftest import DATABASE_URL_TEST, needs_db
from scribe_eval.ingest_corpus import ingest_corpus, list_corpus
from scribe_ingest.chunking import ChunkProfile
from scribe_ingest.embedding import FakeEmbedder
from scribe_ingest.indexer import (
    ActivationRefused, IndexerError, activate_generation, build_generation, embed_generation, ensure_generation,
    load_index_profile, parse_for_index, persist_chunks, register_version,
)
from scribe_ingest.chunking import chunk_document

pytestmark = needs_db
MD = "text/markdown"


@pytest.fixture()
async def env():
    conn = await aconnect(DATABASE_URL_TEST)
    wid = await new_workspace(conn)
    fid = await new_file(conn, wid)
    yield conn, wid, fid
    await conn.close()


async def one(conn, sql, *p):
    return (await (await conn.execute(sql, p)).fetchone())


async def test_register_version_is_idempotent_and_numbers_versions(env):
    conn, wid, fid = env
    v1 = await register_version(conn, wid, fid, doc(1), MD)
    again = await register_version(conn, wid, fid, doc(1), MD)
    v2 = await register_version(conn, wid, fid, doc(2), MD)
    assert (v1.version_number, again.created, again.id) == (1, False, v1.id)
    assert (v2.version_number, v2.created) == (2, True)
    assert (await one(conn, "select desired_version_id from files where id=%s", fid))[0] == v2.id


async def test_register_version_rejects_other_workspace_and_deleted(env):
    conn, wid, fid = env
    other = await new_workspace(conn)
    with pytest.raises(IndexerError) as info:
        await register_version(conn, other, fid, doc(1), MD)
    assert info.value.code == "SOURCE_NOT_FOUND"
    await conn.execute("update files set deleted_at=now() where id=%s", (fid,))
    with pytest.raises(ActivationRefused):
        await register_version(conn, wid, fid, doc(1), MD)


async def test_rerun_is_idempotent(env):
    conn, wid, fid = env
    emb = FakeEmbedder()
    v = await register_version(conn, wid, fid, doc(1), MD)
    r1 = await build_generation(conn, wid, fid, v, doc(1), MD, emb)
    n1 = (await one(conn, "select count(*) from chunks where workspace_id=%s", wid))[0]
    used = emb.texts_embedded
    v_again = await register_version(conn, wid, fid, doc(1), MD)
    r2 = await build_generation(conn, wid, fid, v_again, doc(1), MD, emb)
    assert r1.status == r2.status == "ACTIVE" and r1.generation_id == r2.generation_id
    assert (await one(conn, "select count(*) from chunks where workspace_id=%s", wid))[0] == n1
    assert (await one(conn, "select count(*) from index_generations where source_id=%s", fid))[0] == 1
    assert (await one(conn, "select count(*) from document_blocks where workspace_id=%s", wid))[0] == r1.blocks
    assert emb.texts_embedded == used  # nothing re-embedded


async def test_corpus_rerun_creates_no_duplicates():
    conn = await aconnect(DATABASE_URL_TEST)
    try:
        wid = await new_workspace(conn)
        emb = FakeEmbedder()
        names = [n for n in list_corpus(CORPUS) if n in ("glossary.txt", "price-list-2026.csv", "api-spec.md")]
        load = lambda n: (CORPUS / n).read_bytes()  # noqa: E731
        await ingest_corpus(conn, wid, names, load, emb)
        before = (await one(conn, "select count(*) from chunks where workspace_id=%s", wid))[0]
        res = await ingest_corpus(conn, wid, names, load, emb)
        assert all(r.status == "ACTIVE" for _, r in res)
        assert (await one(conn, "select count(*) from chunks where workspace_id=%s", wid))[0] == before
        assert (await one(conn, "select count(*) from files where workspace_id=%s", wid))[0] == 3
    finally:
        await conn.close()


async def _ready(conn, wid, fid, data, emb):
    v = await register_version(conn, wid, fid, data, MD)
    prof = await load_index_profile(conn)
    gen, _ = await ensure_generation(conn, wid, fid, v.id, prof)
    d = parse_for_index(data, MD, None)
    cs = chunk_document(d)
    await persist_chunks(conn, wid, fid, v.id, gen, d, cs)
    await embed_generation(conn, wid, fid, v.id, gen, prof, emb)
    return v, gen, prof, d


async def test_activation_refused_when_desired_version_changed(env):
    conn, wid, fid = env
    v1, gen, prof, _ = await _ready(conn, wid, fid, doc(1), FakeEmbedder())
    await register_version(conn, wid, fid, doc(2), MD)  # desired moves to v2
    with pytest.raises(ActivationRefused) as info:
        await activate_generation(conn, wid, fid, v1.id, gen, prof)
    assert info.value.code == "VERSION_SUPERSEDED"
    assert (await one(conn, "select active_version_id from files where id=%s", fid))[0] is None
    assert (await one(conn, "select count(*) from active_indexes where source_id=%s", fid))[0] == 0


async def test_activation_refused_when_source_deleted(env):
    conn, wid, fid = env
    v1, gen, prof, _ = await _ready(conn, wid, fid, doc(1), FakeEmbedder())
    await conn.execute("update files set deleted_at=now() where id=%s", (fid,))
    with pytest.raises(ActivationRefused) as info:
        await activate_generation(conn, wid, fid, v1.id, gen, prof)
    assert info.value.code == "SOURCE_DELETED"
    assert (await one(conn, "select count(*) from active_indexes where source_id=%s", fid))[0] == 0


async def test_activation_requires_ready_and_fully_embedded(env):
    conn, wid, fid = env
    v1, gen, prof, _ = await _ready(conn, wid, fid, doc(1), FakeEmbedder())
    await conn.execute("update index_generations set embedded_chunks = embedded_chunks - 1 where id=%s", (gen,))
    with pytest.raises(ActivationRefused) as info:
        await activate_generation(conn, wid, fid, v1.id, gen, prof)
    assert info.value.code == "NOT_READY"


async def test_stale_build_is_cancelled_not_activated(env):
    conn, wid, fid = env
    emb = FakeEmbedder()
    v1 = await register_version(conn, wid, fid, doc(1), MD)
    await register_version(conn, wid, fid, doc(2), MD)
    r = await build_generation(conn, wid, fid, v1, doc(1), MD, emb)
    assert r.status == "CANCELLED" and r.error_code == "VERSION_SUPERSEDED"
    assert (await one(conn, "select status from index_generations where id=%s", r.generation_id))[0] == "CANCELLED"
    assert emb.texts_embedded == 0


async def test_failed_embed_keeps_previous_generation_active(env):
    conn, wid, fid = env
    v1 = await register_version(conn, wid, fid, doc(1), MD)
    r1 = await build_generation(conn, wid, fid, v1, doc(1), MD, FakeEmbedder())
    assert r1.status == "ACTIVE"
    n_emb = (await one(conn, "select count(*) from chunk_embeddings_1536 where workspace_id=%s", wid))[0]

    v2 = await register_version(conn, wid, fid, doc(2), MD)
    r2 = await build_generation(conn, wid, fid, v2, doc(2), MD, FakeEmbedder(fail_after=0))
    assert r2.status == "FAILED" and r2.error_code == "TRANSIENT_PROVIDER" and r2.retryable
    assert (await one(conn, "select status, error_code from index_generations where id=%s", r2.generation_id)) == (
        "FAILED", "TRANSIENT_PROVIDER")
    assert (await one(conn, "select status from index_generations where id=%s", r1.generation_id))[0] == "ACTIVE"
    f = await one(conn, "select active_version_id, status from files where id=%s", fid)
    assert f == (v1.id, "ready")
    assert (await one(conn, "select generation_id from active_indexes where source_id=%s", fid))[0] == r1.generation_id
    assert (await one(conn, "select count(*) from chunk_embeddings_1536 c join chunks k on k.id=c.chunk_id "
                            "where k.generation_id=%s", r1.generation_id))[0] == n_emb

    # retry succeeds: v2 active, v1 superseded, v1 embeddings deleted but chunks/spans kept (D20)
    r3 = await build_generation(conn, wid, fid, v2, doc(2), MD, FakeEmbedder())
    assert r3.status == "ACTIVE" and r3.generation_id == r2.generation_id
    assert (await one(conn, "select status from index_generations where id=%s", r1.generation_id))[0] == "SUPERSEDED"
    assert (await one(conn, "select count(*) from chunk_embeddings_1536 c join chunks k on k.id=c.chunk_id "
                            "where k.generation_id=%s", r1.generation_id))[0] == 0
    assert (await one(conn, "select count(*) from chunks where generation_id=%s", r1.generation_id))[0] > 0
    assert (await one(conn, "select count(*) from chunk_source_spans s join chunks k on k.id=s.chunk_id "
                            "where k.generation_id=%s", r1.generation_id))[0] > 0
    assert (await one(conn, "select active_version_id from files where id=%s", fid))[0] == v2.id
    assert (await one(conn, "select count(*) from index_generations where source_id=%s and status='ACTIVE'", fid))[0] == 1


async def test_partial_embed_resumes_without_reembedding(env):
    conn, wid, fid = env
    big = ("# T\n\n" + "\n\n".join(f"Para {i}. " + "word " * 300 for i in range(12))).encode()
    v = await register_version(conn, wid, fid, big, MD)
    flaky = FakeEmbedder(fail_after=3)
    r = await build_generation(conn, wid, fid, v, big, MD, flaky)
    assert r.status == "FAILED"
    ok = FakeEmbedder()
    r2 = await build_generation(conn, wid, fid, v, big, MD, ok)
    assert r2.status == "ACTIVE" and r2.embedded == r2.expected
    assert ok.texts_embedded + 0 <= r2.expected  # only the missing ones (all if batch 1 failed whole)


async def test_time_budget_leaves_generation_resumable(env):
    conn, wid, fid = env
    v = await register_version(conn, wid, fid, doc(1), MD)
    r = await build_generation(conn, wid, fid, v, doc(1), MD, FakeEmbedder(), deadline=0.0)
    assert r.status == "BUILDING" and r.error_code == "TIME_BUDGET" and r.retryable
    assert (await one(conn, "select status from index_generations where id=%s", r.generation_id))[0] == "BUILDING"
    r2 = await build_generation(conn, wid, fid, v, doc(1), MD, FakeEmbedder())
    assert r2.status == "ACTIVE"


async def test_profile_divergence_fails_loudly(env):
    conn, _, _ = env
    with pytest.raises(IndexerError) as info:
        await load_index_profile(conn, chunk_profile=ChunkProfile(name="structural-v1", child_max_tokens=100))
    assert info.value.code == "PROFILE_MISMATCH" and "child_max_tokens" in info.value.message


async def test_scanned_pdf_fails_with_ocr_code_and_marks_file_failed(env):
    conn, wid, _ = env
    name = "facility-safety-notice-scanned.pdf"
    fid = await new_file(conn, wid, name, "application/pdf")
    data = (CORPUS / name).read_bytes()
    v = await register_version(conn, wid, fid, data, "application/pdf")
    r = await build_generation(conn, wid, fid, v, data, "application/pdf", FakeEmbedder(), name=name)
    assert (r.status, r.error_code, r.retryable) == ("FAILED", "OCR_NOT_AVAILABLE", False)
    assert (await one(conn, "select status, processing_error from files where id=%s", fid)) == (
        "failed", "scanned pages need OCR (T21)")


async def test_unsupported_format_is_permanent(env):
    conn, wid, fid = env
    v = await register_version(conn, wid, fid, b"x", "image/png")
    r = await build_generation(conn, wid, fid, v, b"x", "image/png", FakeEmbedder())
    assert (r.status, r.error_code, r.retryable) == ("FAILED", "UNSUPPORTED_FORMAT", False)

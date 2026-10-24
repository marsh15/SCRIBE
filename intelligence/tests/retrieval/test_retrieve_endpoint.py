"""T13: POST /retrieve (auth, validation, tenancy from token, traces, error mapping, golden queries)."""
from __future__ import annotations

import time

import httpx
import jwt
import pytest
from _t13 import MD, conn_for, corpus_workspace, seed_sync  # noqa: F401  (sets sys.path for _db)
from _db import GOLDEN
from conftest import DATABASE_URL_TEST, SECRET, needs_db
from scribe_ingest.embedding import EmbeddingError, FakeEmbedder

pytestmark = needs_db


def token(wid: str, scope: str = "retrieve") -> str:
    now = int(time.time())
    return jwt.encode({"sub": "user_1", "wid": wid, "scope": scope, "aud": "scribe-intel", "iat": now, "exp": now + 60},
                      SECRET, algorithm="HS256")


def auth(wid, scope="retrieve"):
    return {"authorization": f"Bearer {token(wid, scope)}"}


@pytest.fixture(scope="module")
def seeded():
    return seed_sync(DATABASE_URL_TEST)


@pytest.fixture(scope="module")
def corpus():
    return corpus_workspace(DATABASE_URL_TEST)


@pytest.fixture()
async def client(monkeypatch):
    from scribe_intel import db
    from scribe_intel.app import create_app
    from scribe_intel.config import get_settings

    monkeypatch.setenv("DATABASE_URL", DATABASE_URL_TEST)
    monkeypatch.setenv("SCRIBE_EMBEDDER", "fake")
    monkeypatch.setenv("SCRIBE_ALLOW_FAKE_EMBEDDINGS", "1")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c
    await db.close_pool()
    get_settings.cache_clear()


@pytest.fixture()
async def conn():
    c = await conn_for(DATABASE_URL_TEST)
    yield c
    await c.close()


async def test_success_shape_on_both_paths(client, seeded):
    for path in ("/api/py/retrieve", "/retrieve"):
        r = await client.post(path, json={"query": "ERR_AUTH_403", "profile": "lexical-v1"}, headers=auth(seeded["wa"]))
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["profile"] == "lexical-v1" and j["degraded"] == [] and j["trace_id"]
        top = j["candidates"][0]
        assert top["rank"] == 1 and top["method"] == "lexical" and top["doc_name"] == "api-errors.md"
        assert top["lexical_rank"] == 1 and top["dense_rank"] is None and top["score"] > 0
        assert top["source_id"] == seeded["a_ids"]["api-errors.md"]
        assert top["source_version_id"] and top["generation_id"] and top["provenance"]["chunk_id"] == top["chunk_id"]
        assert top["spans"] and {"page", "bbox", "char_start", "char_end", "block_id"} <= set(top["spans"][0])
        assert "ERR_AUTH_403" in top["content"]
        assert {"lexical_plus_embed_wait", "hydrate", "trace_persist", "request_total"} <= set(j["stage_ms"])
        assert isinstance(j["config_hash"], str) and j["config_hash"]


async def test_default_profile_is_dense_exact(client, seeded):
    r = await client.post("/api/py/retrieve", json={"query": "annual leave days"}, headers=auth(seeded["wa"]))
    assert r.status_code == 200
    j = r.json()
    assert j["profile"] == "dense-exact" and j["meta"]["dense_mode"] == "exact"
    assert 0 < len(j["candidates"]) <= 10
    assert all(c["method"] == "dense" and c["dense_rank"] == c["rank"] for c in j["candidates"])
    assert {"embed", "dense", "hydrate"} <= set(j["stage_ms"])


async def test_dense_ann_and_limit_and_source_ids(client, seeded):
    sid = seeded["a_ids"]["hr.md"]
    r = await client.post("/api/py/retrieve", json={"query": "x", "profile": "dense-ann", "limit": 2, "source_ids": [sid]},
                          headers=auth(seeded["wa"]))
    j = r.json()
    assert r.status_code == 200 and j["meta"]["dense_mode"] == "ann" and len(j["candidates"]) <= 2
    assert {c["source_id"] for c in j["candidates"]} == {sid}


async def test_auth_and_scope(client, seeded):
    body = {"query": "leave"}
    assert (await client.post("/api/py/retrieve", json=body)).status_code == 401
    assert (await client.post("/api/py/retrieve", json=body, headers=auth(seeded["wa"], "ingest health"))).status_code == 403
    r = await client.post("/api/py/retrieve", json=body, headers={"authorization": "Bearer nope"})
    assert r.status_code == 401


@pytest.mark.parametrize("body", [
    {}, {"query": ""}, {"query": "   "}, {"query": "x" * 2001}, {"query": "ok", "limit": 0}, {"query": "ok", "limit": 51},
    {"query": "ok", "source_ids": ["a"]}, {"query": 5}, {"query": "ok", "profile": 7}])
async def test_validation_errors_are_422(client, seeded, body):
    r = await client.post("/api/py/retrieve", json=body, headers=auth(seeded["wa"]))
    assert r.status_code == 422, (body, r.text)


async def test_unknown_profile_is_404(client, seeded):
    r = await client.post("/api/py/retrieve", json={"query": "leave", "profile": "hybrid-z"}, headers=auth(seeded["wa"]))
    assert r.status_code == 404 and r.json()["error_code"] == "UNKNOWN_PROFILE"
    assert "dense-exact" in r.json()["profiles"]


async def test_workspace_comes_from_token_not_body(client, seeded):
    b_chunks = set(seeded["b_chunks"])
    for profile in ("lexical-v1", "dense-exact", "dense-ann"):
        # token for B, body claims A (and a foreign source id): only B's data may come back
        r = await client.post("/api/py/retrieve",
                              json={"query": "ERR_AUTH_403 annual leave", "profile": profile, "workspace_id": seeded["wa"],
                                    "source_ids": [seeded["a_ids"]["api-errors.md"], seeded["b_ids"]["api-errors.md"]]},
                              headers=auth(seeded["wb"]))
        assert r.status_code == 200, r.text
        got = r.json()["candidates"]
        assert {c["chunk_id"] for c in got} <= {str(x) for x in b_chunks}
        assert {c["source_id"] for c in got} <= {seeded["b_ids"]["api-errors.md"]}
        r = await client.post("/api/py/retrieve", json={"query": "ERR_AUTH_403", "profile": profile, "workspace_id": seeded["wb"]},
                              headers=auth(seeded["wa"]))
        assert {c["source_id"] for c in r.json()["candidates"]} <= set(seeded["a_ids"].values())


async def test_trace_and_candidates_persisted(client, seeded, conn):
    r = await client.post("/api/py/retrieve", json={"query": "annual leave", "profile": "dense-exact", "limit": 3},
                          headers=auth(seeded["wa"]))
    j = r.json()
    cur = await conn.execute("select workspace_id, query, status, degraded, stage_ms, counts, config_hash, cost "
                             "from retrieval_traces where id=%s", (j["trace_id"],))
    wid, query, status, degraded, stage_ms, counts, cfg, cost = await cur.fetchone()
    assert str(wid) == seeded["wa"] and query == "annual leave" and status == "ok" and degraded is None
    assert counts["profile"] == "dense-exact" and counts["returned"] == 3 and counts["dense"] >= 3 and cfg
    assert stage_ms["dense"] >= 0
    assert cost == j["cost"]
    assert cost["embed_tokens"] > 0 and cost["rerank_tokens"] == 0
    assert cost["usd"] == 0.0 and cost["estimated"] is True
    assert cost["prices_as_of"] == "2026-10-21"
    cur = await conn.execute("select chunk_id, dense_rank, dense_score, lexical_rank, selected from retrieval_candidates "
                             "where trace_id=%s order by dense_rank", (j["trace_id"],))
    rows = await cur.fetchall()
    assert len(rows) >= 3 and [x[1] for x in rows] == list(range(1, len(rows) + 1))
    assert [str(x[0]) for x in rows[:3]] == [c["chunk_id"] for c in j["candidates"]]
    assert [x[4] for x in rows[:3]] == [True] * 3 and not any(x[4] for x in rows[3:])
    assert all(x[3] is None for x in rows)
    r = await client.post("/api/py/retrieve", json={"query": "ERR_AUTH_403", "profile": "lexical-v1"}, headers=auth(seeded["wa"]))
    cur = await conn.execute("select lexical_rank, lexical_score, dense_rank from retrieval_candidates where trace_id=%s "
                             "order by lexical_rank", (r.json()["trace_id"],))
    rows = await cur.fetchall()
    assert rows[0][0] == 1 and rows[0][1] > 0 and rows[0][2] is None


async def test_embedding_error_is_503_never_empty_and_trace_failed(client, seeded, conn, monkeypatch):
    from scribe_intel import retrieve_routes

    def factory(_dsn):
        return lambda dims: FakeEmbedder(dims, fail_after=0)

    monkeypatch.setattr(retrieve_routes, "make_embedder_for", factory)
    # make the fail-fast probe in the route pass but fail on use
    r = await client.post("/api/py/retrieve", json={"query": "annual leave SECRETQUERYTEXT"}, headers=auth(seeded["wa"]))
    assert r.status_code == 503, r.text
    j = r.json()
    assert j["error_code"] == "RETRIEVAL_FAILED" and j["retryable"] is True and j["cause"] == "TRANSIENT_PROVIDER"
    assert "candidates" not in j and "SECRETQUERYTEXT" not in r.text
    cur = await conn.execute("select status from retrieval_traces where id=%s", (j["trace_id"],))
    assert (await cur.fetchone())[0] == "failed"
    # lexical-only strategy needs no embedding: still fine
    r = await client.post("/api/py/retrieve", json={"query": "annual leave", "profile": "lexical-v1"}, headers=auth(seeded["wa"]))
    assert r.status_code == 200


async def test_database_error_is_503(client, seeded, monkeypatch):
    from scribe_intel.retrieval import registry

    async def boom(*a, **k):
        import psycopg
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(registry, "run_retrievers", boom)
    r = await client.post("/api/py/retrieve", json={"query": "annual leave", "profile": "lexical-v1"}, headers=auth(seeded["wa"]))
    assert r.status_code == 503 and r.json()["error_code"] == "RETRIEVAL_FAILED"


async def test_trace_persist_failure_degrades_but_does_not_fail(client, seeded, monkeypatch, caplog):
    from scribe_intel import retrieve_routes

    async def boom(*a, **k):
        raise RuntimeError("disk full: annual leave")

    monkeypatch.setattr(retrieve_routes, "persist_trace", boom)
    with caplog.at_level("INFO"):
        r = await client.post("/api/py/retrieve", json={"query": "annual leave", "profile": "lexical-v1"},
                              headers=auth(seeded["wa"]))
    assert r.status_code == 200
    j = r.json()
    assert "trace_persist_failed" in j["degraded"] and j["trace_id"] is None and j["candidates"]
    assert "annual leave" not in caplog.text  # INV-13: no query/document text in logs


async def test_fake_embedder_refused_without_flag(client, seeded, monkeypatch):
    monkeypatch.delenv("SCRIBE_ALLOW_FAKE_EMBEDDINGS")
    r = await client.post("/api/py/retrieve", json={"query": "x"}, headers=auth(seeded["wa"]))
    assert r.status_code == 500 and r.json()["error_code"] == "FAKE_EMBEDDER_REFUSED"


async def test_legacy_dense_strategy_endpoint(client, conn, monkeypatch):
    from _db import new_workspace
    from _t13 import seed_legacy, unit3072

    wid = await new_workspace(conn)
    await seed_legacy(conn, wid, "l.txt", [0.9, 0.5, 0.2])
    # the fake embedder's 3072-d query vector is random, so pin it to e0 for a deterministic expectation
    async def e0(self, texts):
        return [unit3072(1.0, 1) for _ in texts]

    monkeypatch.setattr(FakeEmbedder, "embed_queries", e0)
    r = await client.post("/api/py/retrieve", json={"query": "anything", "profile": "legacy-dense"}, headers=auth(wid))
    assert r.status_code == 200, r.text
    j = r.json()
    assert [c["method"] for c in j["candidates"]] == ["dense-legacy"] * 2  # 0.2 is below the 0.3 threshold
    assert [round(c["score"], 3) for c in j["candidates"]] == [0.9, 0.5]
    assert j["meta"]["threshold"] == 0.3


def _golden_questions():
    import json

    return [json.loads(line)["question"] for line in GOLDEN.read_text().splitlines() if line.strip()]


@pytest.mark.parametrize("profile", ["lexical-v1", "dense-exact", "dense-ann"])
async def test_every_golden_query_runs_on_the_full_corpus(client, corpus, profile):
    """Smoke only: the fake embedder has no semantics, so NO retrieval quality is asserted or reported."""
    qs = _golden_questions()
    assert len(qs) >= 100
    ok = nonempty = 0
    for q in qs:
        r = await client.post("/api/py/retrieve", json={"query": q, "profile": profile, "limit": 20}, headers=auth(corpus["wid"]))
        assert r.status_code == 200, (q, r.text)
        j = r.json()
        assert j["degraded"] == [] and len(j["candidates"]) <= 20
        ok += 1
        nonempty += bool(j["candidates"])
    assert ok == len(qs)
    if profile.startswith("dense"):
        assert nonempty == len(qs)  # dense always has neighbours in a non-empty index

"""Lexical channel wiring. With the original `and` lexical mode most natural-language questions return an EMPTY lexical
list, so the "hybrid" profile degenerates to dense search (m1-prelim.md section 6). The `or_rank` profiles fix that
structurally. These tests pin WHICH profile the app and the regression gate actually serve, and show on the fixture
corpus (fake embeddings) that the served hybrid really uses the lexical channel. They say nothing about ranking quality:
that is NOT MEASURED until the golden set is reviewed (docs/scribe2/benchmarks/final.md).

The dev split is read only to pick example questions; nothing is tuned and the test split is never touched."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
import jwt
import pytest
from _t13 import corpus_workspace  # noqa: F401  (sets sys.path for _db)
from conftest import DATABASE_URL_TEST, SECRET, needs_db
from scribe_eval import gate
from scribe_intel.retrieval.profiles import BY_NAME

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "benchmarks" / "datasets" / "golden-v1" / "cases.jsonl"
SERVED = "hybrid-rrf-rerank-v2"


def test_the_gate_guards_the_or_rank_hybrid_profile():
    assert gate.DEFAULT_PROFILE == SERVED
    assert BY_NAME[SERVED].config.lexical_mode == "or_rank"
    assert BY_NAME[SERVED].config.dense_top_k > 0 and BY_NAME[SERVED].config.lexical_top_k > 0  # really hybrid


def test_the_app_serves_the_profile_the_gate_guards():
    ts = (REPO / "lib" / "python-retrieval.ts").read_text()
    served = re.search(r'export const EVIDENCE_PROFILE = "([^"]+)"', ts)
    assert served and served.group(1) == gate.DEFAULT_PROFILE


def test_the_and_mode_profiles_are_still_there_unchanged():
    # older profiles are immutable (stored rows are hashed): the fix adds a default, it does not edit history
    assert BY_NAME["hybrid-rrf-rerank"].config.lexical_mode == "and"
    assert BY_NAME["hybrid-rrf"].config.lexical_mode == "and"


def token(wid: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": "user_1", "wid": wid, "scope": "retrieve", "aud": "scribe-intel", "iat": now, "exp": now + 60},
                      SECRET, algorithm="HS256")


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
    monkeypatch.setenv("SCRIBE_RERANKER", "fake")
    monkeypatch.setenv("SCRIBE_ALLOW_FAKE_RERANKER", "1")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c
    await db.close_pool()
    get_settings.cache_clear()


def dev_questions(n: int = 20) -> list[str]:
    cases = [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    dev = sorted((c for c in cases if c["split"] == "dev" and c["answerable"]), key=lambda c: c["id"])
    return [c["question"] for c in dev[:n]]


async def retrieve(client, wid, profile, query):
    r = await client.post("/api/py/retrieve", json={"query": query, "profile": profile, "limit": 20},
                          headers={"authorization": f"Bearer {token(wid)}"})
    assert r.status_code == 200, r.text
    return r.json()


async def candidates(client, wid, profile, query):
    return (await retrieve(client, wid, profile, query))["candidates"]


@needs_db
async def test_served_hybrid_uses_the_lexical_channel_where_and_mode_hybrid_does_not(client, corpus):
    """Same questions, same corpus, no rerank in either arm so only the lexical mode differs."""
    with_lexical = {"and": 0, "or_rank": 0}
    for q in dev_questions():
        old = await candidates(client, corpus["wid"], "hybrid-rrf", q)
        new = await candidates(client, corpus["wid"], "hybrid-rrf-v2", q)
        with_lexical["and"] += any(c["lexical_rank"] for c in old)
        with_lexical["or_rank"] += any(c["lexical_rank"] for c in new)
    assert with_lexical["and"] <= 10, with_lexical  # most natural questions get no lexical candidates in `and` mode
    assert with_lexical["or_rank"] >= 18, with_lexical  # the or_rank hybrid has lexical candidates for nearly all
    assert with_lexical["or_rank"] > with_lexical["and"]


@needs_db
async def test_candidate_order_does_not_depend_on_chunk_uuids(client, corpus):
    """A second, independent ingest of the same corpus generates new random chunk UUIDs. The served hybrid must return the
    same chunks in the same order, or the cached reranker inputs of the regression gate would miss on unchanged code."""
    import asyncio

    from _db import CORPUS, aconnect, new_workspace
    from scribe_eval.ingest_corpus import ingest_corpus, list_corpus
    from scribe_ingest.embedding import FakeEmbedder

    async def second():
        conn = await aconnect(DATABASE_URL_TEST)
        try:
            wid = await new_workspace(conn)
            await ingest_corpus(conn, wid, list_corpus(CORPUS), lambda n: (CORPUS / n).read_bytes(), FakeEmbedder())
            return wid
        finally:
            await conn.close()

    other = await second()
    differing = []
    for q in dev_questions():
        for profile in ("hybrid-rrf-v2", "hybrid-rrf"):
            a = [(c["doc_name"], c["content_hash"]) for c in await candidates(client, corpus["wid"], profile, q)]
            b = [(c["doc_name"], c["content_hash"]) for c in await candidates(client, other, profile, q)]
            if a != b:
                differing.append((profile, q))
    assert not differing, f"{len(differing)} question/profile pairs order chunks by random UUID, e.g. {differing[:2]}"


@needs_db
async def test_the_served_profile_works_without_a_seeded_profile_row_and_its_trace_records_the_configuration(client, corpus):
    """Migration 0012 seeds profiles A-E only; the v2 profiles live in code. Retrieval must not depend on the stored row, and
    the trace must still say which configuration ran (profile name in `counts`, a configuration hash; profile_id is NULL when the row is absent)."""
    import psycopg

    q = dev_questions(1)[0]
    body = await retrieve(client, corpus["wid"], SERVED, q)
    assert body["candidates"], "the served profile returned nothing for a fixture question"
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        row = c.execute("select config_hash, profile_id, counts->>'profile' from retrieval_traces where id = %s", (body["trace_id"],)).fetchone()
    other = await retrieve(client, corpus["wid"], "hybrid-rrf-rerank", q)
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        other_hash = c.execute("select config_hash from retrieval_traces where id = %s", (other["trace_id"],)).fetchone()[0]
    # the trace names the profile and its configuration hash differs from the AND-mode profile's for the same request
    assert row[2] == SERVED and row[0] and row[0] != other_hash

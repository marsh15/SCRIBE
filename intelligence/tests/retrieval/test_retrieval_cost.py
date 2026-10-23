"""C3 retrieval trace pricing: shared pure prices and offline fake-provider route persistence."""
from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from scribe_intel import retrieve_routes
from scribe_intel.auth import Principal
from scribe_intel.pricing import PRICE_TABLE_DATE, UNIT_INPUT, UNIT_OUTPUT, UNIT_TOKENS, price_usd, tokens_usd
from scribe_intel.retrieval.registry import StrategyResult
from scribe_intel.retrieval.rerank import FakeReranker


def test_shared_price_table_and_arithmetic_keep_verified_values():
    from scribe_eval import cost

    assert cost.PRICES is __import__("scribe_intel.pricing", fromlist=["PRICES"]).PRICES
    assert cost.PRICE_TABLE_DATE == PRICE_TABLE_DATE == "2026-10-21"
    assert price_usd("gemini-embedding-2", UNIT_TOKENS) == 0.20
    assert price_usd("gemini-2.5-pro", UNIT_INPUT) == 1.25
    assert price_usd("gemini-2.5-pro", UNIT_OUTPUT) == 10.0
    assert price_usd("gemini-2.5-flash", UNIT_INPUT) == 0.30
    assert price_usd("gemini-2.5-flash", UNIT_OUTPUT) == 2.50
    assert price_usd("gemini-2.5-flash-lite", UNIT_INPUT) == 0.10
    assert price_usd("gemini-2.5-flash-lite", UNIT_OUTPUT) == 0.40
    assert price_usd("rerank-2.5", UNIT_TOKENS) == 0.05
    assert "UNVERIFIED" in next(p.source_note for p in cost.PRICES if p.model_id == "gemini-embedding-001")
    assert tokens_usd("rerank-2.5", 2_000_000) == pytest.approx(0.10)
    assert tokens_usd("fake-reranker", 10**9) == 0.0
    tracked_cost = retrieve_routes._RetrievalCost()
    tracked_cost.add("gemini-embedding-2", 100, is_fake=True)
    assert tracked_cost.usd == 0.0


@pytest.mark.asyncio
async def test_cached_rerank_and_no_key_embedding_are_not_charged():
    from scribe_ingest.embedding import EmbeddingError

    class CacheHitReranker:
        model = "rerank-2.5"
        is_fake = False
        hits = 0

        class Provider:
            requests_made = 1

        inner = Provider()

        async def rerank(self, query, docs, top_n):
            self.hits += 1
            return []

    cache_cost = retrieve_routes._RetrievalCost()
    cached = retrieve_routes._TrackedReranker(CacheHitReranker(), cache_cost)
    await cached.rerank("query", ["sample document"], 1)
    assert cache_cost.rerank_tokens > 0 and cache_cost.usd == 0.0

    class NoKeyEmbedder:
        model = "gemini-embedding-2"
        dimensions = 1536
        is_fake = False
        requests_made = 0

        async def embed_queries(self, texts):
            raise EmbeddingError("AUTH", "missing key", retryable=False)

    failed_cost = retrieve_routes._RetrievalCost()
    embedder = retrieve_routes._TrackedEmbedder(NoKeyEmbedder(), failed_cost)
    with pytest.raises(EmbeddingError):
        await embedder.embed_queries(["query text"])
    assert failed_cost.embed_tokens > 0 and failed_cost.usd == 0.0 and failed_cost.estimated is True


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def executemany(self, query, params):
        return None


class _Conn:
    def __init__(self):
        self.insert_params = None
        self.insert_query = None

    def transaction(self):
        return _Cursor(self)

    def cursor(self):
        return _Cursor(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, params=()):
        if "insert into retrieval_traces" in query:
            self.insert_query = query
            self.insert_params = params
            return _FetchOne()
        return _FetchOne()


class _FetchOne:
    async def fetchone(self):
        return (UUID("00000000-0000-4000-8000-000000000001"),)


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    @asynccontextmanager
    async def connection(self):
        yield self.conn


@pytest.mark.asyncio
async def test_persist_trace_accepts_legacy_call_without_cost_and_stores_sql_null():
    pool = _Pool()
    body = retrieve_routes.RetrieveBody(query="legacy caller")
    await retrieve_routes.persist_trace(pool, "00000000-0000-4000-8000-000000000002", body, "ok",
                                        result=None, returned=0, stage_ms={}, degraded=[])
    assert "stage_ms, counts, cost," in pool.conn.insert_query
    assert pool.conn.insert_params[7] is None


@pytest.mark.asyncio
async def test_route_persists_fake_provider_cost_in_scoped_trace_transaction(monkeypatch):
    pool = _Pool()
    wid = "00000000-0000-4000-8000-000000000002"

    class Settings:
        dsn = "postgresql://scribe:scribe@localhost/scribe_test"

    from scribe_ingest.embedding import FakeEmbedder

    monkeypatch.setattr(retrieve_routes, "get_settings", lambda: Settings())
    monkeypatch.setattr(retrieve_routes, "get_pool", lambda: _async_value(pool))
    monkeypatch.setattr(retrieve_routes, "make_embedder_for", lambda dsn: lambda dims: FakeEmbedder(dims))
    fake_reranker = FakeReranker()
    monkeypatch.setattr(retrieve_routes, "make_reranker_for", lambda dsn: lambda model: fake_reranker)

    async def handler(ctx):
        await ctx.embedder_for(1536).embed_queries([ctx.query])
        await ctx.reranker_for("rerank-2.5").rerank(ctx.query, ["offline sample text"], 1)
        return StrategyResult(ranked=[], counts={})

    monkeypatch.setattr(retrieve_routes, "get_handler", lambda profile: handler)
    body = retrieve_routes.RetrieveBody(query="offline query")
    response = await retrieve_routes.retrieve(body, Principal("u1", wid, ("retrieve",)))

    assert response["trace_id"] == "00000000-0000-4000-8000-000000000001"
    cost = response["cost"]
    assert set(cost) == {"embed_tokens", "rerank_tokens", "usd", "estimated", "prices_as_of"}
    assert cost["usd"] == 0.0 and cost["estimated"] is True and cost["prices_as_of"] == PRICE_TABLE_DATE
    assert cost["embed_tokens"] > 0 and cost["rerank_tokens"] > 0
    assert "stage_ms, counts, cost," in pool.conn.insert_query
    stored_cost = pool.conn.insert_params[7]
    assert stored_cost.obj == cost


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_optional_provider_metadata_does_not_replace_provider_failures():
    from scribe_ingest.embedding import EmbeddingError

    class BareEmbedder:
        async def embed_queries(self, texts):
            raise EmbeddingError("INVALID_REQUEST", "scripted provider failure", retryable=False)

    cost = retrieve_routes._RetrievalCost()
    tracked = retrieve_routes._costed_embedder_factory(lambda dims: BareEmbedder(), cost)(1536)
    assert tracked.dimensions == 1536
    with pytest.raises(EmbeddingError, match="scripted provider failure"):
        await tracked.embed_queries(["query"])
    assert cost.as_json()["usd"] is None  # no price was reported for this injected provider
    assert cost.as_json()["estimated"] is True

    class BareReranker:
        async def rerank(self, query, docs, top_n):
            return []

    tracked_reranker = retrieve_routes._TrackedReranker(BareReranker(), cost)
    assert await tracked_reranker.rerank("query", ["document"], 1) == []
    assert cost.as_json()["usd"] is None

"""GeminiEmbedder against httpx.MockTransport (no network, no key) + FakeEmbedder."""
from __future__ import annotations

import json
import math

import httpx
import pytest
from scribe_eval.cache import DiskCache
from scribe_eval.embed_cache import DiskEmbeddingCache
from scribe_ingest.embedding import (
    FakeEmbedder, FakeEmbedderRefused, GeminiEmbedder, EmbeddingError, TaskFormat, embedder_from_env,
)

KEY = "AIza-test-key-should-never-leak"


def _vec(n=1536, scale=3.0):
    return [scale * (1 + (i % 7)) for i in range(n)]


def make(handler, **kw):
    sleeps: list[float] = []

    async def sleep(s):
        sleeps.append(s)

    e = GeminiEmbedder(api_key=KEY, transport=httpx.MockTransport(handler), sleep=sleep, **kw)
    return e, sleeps


def ok_handler(calls, dims=1536):
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append((request, body))
        return httpx.Response(200, json={"embeddings": [{"values": _vec(dims)} for _ in body["requests"]]})
    return handler


async def test_batches_of_100_and_request_shape():
    calls: list = []
    e, _ = make(ok_handler(calls))
    out = await e.embed_documents([f"text {i}" for i in range(250)])
    assert len(out) == 250 and len(calls) == 3
    assert [len(b["requests"]) for _, b in calls] == [100, 100, 50]
    for req, body in calls:
        assert req.url.path.endswith("/models/gemini-embedding-2:batchEmbedContents")
        assert all(r["outputDimensionality"] == 1536 and r["model"] == "models/gemini-embedding-2"
                   for r in body["requests"])
        assert "taskType" not in json.dumps(body) and "task_type" not in json.dumps(body)


async def test_vectors_are_l2_normalized():
    e, _ = make(ok_handler([]))
    (v,) = await e.embed_documents(["x"])
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)


async def test_document_and_query_formats():
    calls: list = []
    e, _ = make(ok_handler(calls))
    await e.embed_documents(["body"], ["Policy > Leave"])
    await e.embed_documents(["body"], [None])
    await e.embed_queries(["how long?"])
    texts = [b["requests"][0]["content"]["parts"][0]["text"] for _, b in calls]
    assert texts == ["title: Policy > Leave | text: body", "title: none | text: body",
                     "task: search result | query: how long?"]
    assert TaskFormat().name  # recorded alongside the cache key


async def test_api_key_only_in_header():
    calls: list = []
    e, _ = make(ok_handler(calls))
    await e.embed_documents(["x"])
    req, body = calls[0]
    assert req.headers["x-goog-api-key"] == KEY
    assert KEY not in str(req.url) and KEY not in json.dumps(body)


async def test_bad_dimension_is_an_error():
    e, _ = make(ok_handler([], dims=768))
    with pytest.raises(EmbeddingError) as info:
        await e.embed_documents(["x"])
    assert info.value.code == "INVALID_REQUEST" and not info.value.retryable
    assert KEY not in str(info.value)


async def test_count_mismatch_and_non_finite():
    def short(request):
        return httpx.Response(200, json={"embeddings": []})
    e, _ = make(short)
    with pytest.raises(EmbeddingError, match="expected 1 embeddings"):
        await e.embed_documents(["x"])

    def nan(request):
        v = _vec(); v[3] = None
        return httpx.Response(200, json={"embeddings": [{"values": v}]})
    e, _ = make(nan)
    with pytest.raises(EmbeddingError, match="non-finite"):
        await e.embed_documents(["x"])


async def test_429_retry_then_success_honors_retry_after():
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "slow"}})
        if state["n"] == 2:
            return httpx.Response(429, json={"error": {"details": [{"retryDelay": "3s"}]}})
        return httpx.Response(200, json={"embeddings": [{"values": _vec()}]})
    e, sleeps = make(handler)
    out = await e.embed_documents(["x"])
    assert len(out) == 1 and state["n"] == 3 and sleeps == [7.0, 3.0]


async def test_retries_are_bounded_and_classified():
    n = {"c": 0}

    def handler(request):
        n["c"] += 1
        return httpx.Response(503, json={})
    e, sleeps = make(handler)
    with pytest.raises(EmbeddingError) as info:
        await e.embed_documents(["x"])
    assert info.value.code == "TRANSIENT_PROVIDER" and info.value.retryable
    assert n["c"] == 6 and len(sleeps) == 5  # 1 try + 5 retries


@pytest.mark.parametrize("status,code", [(401, "AUTH"), (403, "AUTH"), (400, "INVALID_REQUEST")])
async def test_permanent_errors_do_not_retry(status, code):
    n = {"c": 0}

    def handler(request):
        n["c"] += 1
        return httpx.Response(status, json={"error": {"message": "nope"}})
    e, _ = make(handler)
    with pytest.raises(EmbeddingError) as info:
        await e.embed_documents(["x"])
    assert info.value.code == code and not info.value.retryable and n["c"] == 1


async def test_missing_key_is_auth_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
    e = GeminiEmbedder(transport=httpx.MockTransport(ok_handler([])))
    with pytest.raises(EmbeddingError) as info:
        await e.embed_documents(["x"])
    assert info.value.code == "AUTH"


async def test_cache_hits_skip_the_provider(tmp_path):
    calls: list = []
    cache = DiskCache(tmp_path / "e.sqlite")
    e, _ = make(ok_handler(calls), cache=DiskEmbeddingCache(cache))
    a = await e.embed_documents(["same", "other"])
    b = await e.embed_documents(["same", "other"])
    assert a == b and len(calls) == 1
    assert cache.stats["hits"] >= 2
    # a different task format must not collide with the same text
    await e.embed_queries(["same"])
    assert len(calls) == 2


async def test_fake_embedder_deterministic_unit_vectors():
    f = FakeEmbedder()
    assert f.is_fake and f.dimensions == 1536
    a = await f.embed_documents(["a", "b"])
    b = await f.embed_documents(["a", "b"])
    assert a == b and a[0] != a[1]
    assert all(len(v) == 1536 and math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-9) for v in a)


def test_fake_refused_on_non_test_database():
    env = {"SCRIBE_EMBEDDER": "fake", "SCRIBE_ALLOW_FAKE_EMBEDDINGS": "1"}
    with pytest.raises(FakeEmbedderRefused):
        embedder_from_env("postgresql://u:p@h:5432/scribe", env)
    with pytest.raises(FakeEmbedderRefused):  # test db but flag missing
        embedder_from_env("postgresql://u:p@h:5432/x_test", {"SCRIBE_EMBEDDER": "fake"})
    assert embedder_from_env("postgresql://u:p@h:5432/x_test", env).is_fake
    assert not embedder_from_env("postgresql://u:p@h/scribe", {"GOOGLE_GENERATIVE_AI_API_KEY": "k"}).is_fake

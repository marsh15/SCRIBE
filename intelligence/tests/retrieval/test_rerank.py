import json

import httpx
import pytest

from _t14_factories import cand
from scribe_intel.retrieval import rerank as rr
from scribe_intel.retrieval.fusion import rrf_fuse
from scribe_intel.retrieval.rerank import (
    CachedReranker, CacheMiss, FakeReranker, RerankError, RerankResult, VoyageReranker, rerank_candidates,
    rerank_cache_key, truncate_to_tokens,
)

KEY = "sk-test-SECRET-123"


async def _nosleep(_):
    return None


def voyage(handler, **kw):
    return VoyageReranker(api_key=KEY, transport=httpx.MockTransport(handler), sleep=_nosleep, **kw)


def ok(data):
    return httpx.Response(200, json={"object": "list", "data": data, "model": "rerank-2.5", "usage": {"total_tokens": 9}})


async def test_request_shape_and_ordering():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["headers"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return ok([{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}])

    out = await voyage(handler).rerank("q", ["a", "b", "c"], 2)
    assert seen["url"] == "https://api.voyageai.com/v1/rerank"
    assert seen["body"] == {"query": "q", "documents": ["a", "b", "c"], "model": "rerank-2.5", "top_k": 2,
                            "return_documents": False, "truncation": True}
    assert seen["headers"]["authorization"] == f"Bearer {KEY}"
    assert KEY not in seen["url"] and KEY not in json.dumps(seen["body"])
    assert out == [RerankResult(1, 0.9), RerankResult(0, 0.2)]


async def test_429_then_success_honours_retry_after():
    calls, sleeps = [], []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, json={"detail": "slow down"})
        return ok([{"index": 0, "relevance_score": 1.0}])

    async def sleep(s):
        sleeps.append(s)

    r = VoyageReranker(api_key=KEY, transport=httpx.MockTransport(handler), sleep=sleep)
    assert (await r.rerank("q", ["a"], 1))[0].index == 0
    assert len(calls) == 2 and sleeps == [2.0]


async def test_401_is_auth_non_retryable_and_never_leaks_key():
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(401, json={"detail": "bad key " + KEY})

    with pytest.raises(RerankError) as e:
        await voyage(handler).rerank("q", ["a"], 1)
    assert e.value.code == "AUTH" and not e.value.retryable and len(n) == 1
    assert KEY not in str(e.value) and KEY not in repr(e.value)


async def test_5xx_retries_then_gives_up_transient():
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(503)

    with pytest.raises(RerankError) as e:
        await voyage(handler, max_retries=2).rerank("q", ["a"], 1)
    assert e.value.code == "TRANSIENT_PROVIDER" and e.value.retryable and len(n) == 3


async def test_429_exhausted_is_rate_limited():
    with pytest.raises(RerankError) as e:
        await voyage(lambda r: httpx.Response(429), max_retries=1).rerank("q", ["a"], 1)
    assert e.value.code == "RATE_LIMITED"


async def test_400_invalid_request_and_malformed_and_missing_key():
    with pytest.raises(RerankError) as e:
        await voyage(lambda r: httpx.Response(400, json={"detail": "bad"})).rerank("q", ["a"], 1)
    assert e.value.code == "INVALID_REQUEST" and not e.value.retryable
    with pytest.raises(RerankError) as e:
        await voyage(lambda r: httpx.Response(200, json={"nope": 1})).rerank("q", ["a"], 1)
    assert e.value.code == "INVALID_REQUEST"
    with pytest.raises(RerankError) as e:
        await voyage(lambda r: ok([{"index": 5, "relevance_score": 1}])).rerank("q", ["a"], 1)
    assert e.value.code == "INVALID_REQUEST"
    with pytest.raises(RerankError) as e:
        await VoyageReranker(api_key="", transport=httpx.MockTransport(lambda r: ok([]))).rerank("q", ["a"], 1)
    assert e.value.code == "AUTH"
    with pytest.raises(RerankError) as e:
        await voyage(lambda r: ok([])).rerank("q", ["x"] * 1001, 1)
    assert e.value.code == "INVALID_REQUEST"


async def test_timeout_is_transient_and_degrades_candidates():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    ranked = rrf_fuse({"dense": [cand(1, 1), cand(2, 2)]})
    out, deg = await rerank_candidates(voyage(handler, max_retries=1), "q", ranked, 2)
    assert deg == ["rerank_unavailable"]
    assert [r.chunk_id for r in out] == [r.chunk_id for r in ranked]
    assert all(r.rerank_rank is None for r in out)


async def test_rerank_candidates_orders_sets_ranks_and_keeps_tail():
    cs = [cand(1, 1, content="alpha"), cand(2, 2, content="beta gamma"), cand(3, 3, content="gamma"), cand(4, 4, content="beta gamma delta")]
    ranked = rrf_fuse({"dense": cs})
    out, deg = await rerank_candidates(FakeReranker(), "beta gamma", ranked, 3)
    assert deg == []
    assert [r.chunk_id.int - 0x1000 for r in out] == [2, 3, 1, 4]  # 2: 1.0, 3: .5, 1: 0; 4 is tail
    assert [r.rerank_rank for r in out] == [1, 2, 3, None]
    assert out[0].rerank_score == 1.0 and out[0].fusion_rank == 2
    assert ranked[0].rerank_rank is None  # inputs not mutated


async def test_docs_sent_have_heading_and_are_truncated():
    seen = {}

    class Spy(FakeReranker):
        async def rerank(self, query, docs, top_n):
            seen["docs"] = docs
            return await super().rerank(query, docs, top_n)

    ranked = rrf_fuse({"dense": [cand(1, 1, content="word " * 500, heading="H1")]})
    await rerank_candidates(Spy(), "q", ranked, 1, max_doc_tokens=50, count_tokens=lambda t: len(t.split()))
    assert seen["docs"][0].startswith("H1\nword")
    assert len(seen["docs"][0].split()) <= 50


def test_truncate_to_tokens():
    ct = lambda t: len(t.split())  # noqa: E731
    assert truncate_to_tokens("a b c", 5, ct) == "a b c"
    assert ct(truncate_to_tokens("a b c d e f", 3, ct)) == 3
    assert ct(truncate_to_tokens("x " * 100, 0, ct)) == 0


async def test_fake_is_deterministic_and_flagged():
    f = FakeReranker()
    assert f.is_fake
    a = await f.rerank("one two", ["one", "none", "one two", "two one"], 4)
    assert a == await f.rerank("one two", ["one", "none", "one two", "two one"], 4)
    assert [r.index for r in a] == [2, 3, 0, 1]  # ties by index


class DictCache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v


async def test_cache_hit_miss_and_replay():
    inner = FakeReranker()
    cache = DictCache()
    c = CachedReranker(inner, cache, mode="record")
    docs = ["alpha beta", "beta", "gamma"]
    first = await c.rerank("beta", docs, 2)
    assert inner.calls == 1 and (c.hits, c.misses) == (0, 1)
    assert await c.rerank("beta", docs, 2) == first and inner.calls == 1 and c.hits == 1
    # different top_n served from the same cached full ranking
    assert len(await c.rerank("beta", docs, 3)) == 3 and inner.calls == 1
    replay = CachedReranker(FakeReranker(), cache, mode="replay")
    assert await replay.rerank("beta", docs, 2) == first
    with pytest.raises(CacheMiss):
        await replay.rerank("other query", docs, 2)
    assert replay.inner.calls == 0


async def test_cache_with_diskcache(tmp_path):
    scribe_eval = pytest.importorskip("scribe_eval.cache")
    dc = scribe_eval.DiskCache(tmp_path / "c.sqlite", mode="record")
    inner = FakeReranker()
    c = CachedReranker(inner, dc)
    docs = ["a b", "b"]
    r1 = await c.rerank("b", docs, 2)
    assert await c.rerank("b", docs, 2) == r1 and inner.calls == 1
    assert rerank_cache_key("fake-reranker", "b", docs) == scribe_eval.rerank_key(
        "fake-reranker", "b", [scribe_eval.text_hash(d) for d in docs])
    assert rr.CacheMiss is scribe_eval.CacheMiss
    dc.close()


async def test_cache_miss_propagates_not_degraded():
    c = CachedReranker(FakeReranker(), DictCache(), mode="replay")
    ranked = rrf_fuse({"dense": [cand(1, 1)]})
    with pytest.raises(CacheMiss):
        await rerank_candidates(c, "q", ranked, 1)


async def test_invalid_indices_from_reranker_degrade():
    class Bad:
        is_fake = True
        model = "bad"

        async def rerank(self, q, d, n):
            return [RerankResult(7, 1.0)]

    ranked = rrf_fuse({"dense": [cand(1, 1)]})
    _, deg = await rerank_candidates(Bad(), "q", ranked, 1)
    assert deg == ["rerank_unavailable"]


async def test_any_error_code_degrades():
    for code in ("RATE_LIMITED", "TRANSIENT_PROVIDER", "AUTH", "INVALID_REQUEST"):
        ranked = rrf_fuse({"dense": [cand(1, 1)]})
        _, deg = await rerank_candidates(FakeReranker(fail_with=RerankError(code, "x", retryable=False)), "q", ranked, 1)  # type: ignore[arg-type]
        assert deg == ["rerank_unavailable"]

"""T14: hybrid profiles A-E served by POST /retrieve (offline: fake embedder, fake/mocked rerankers only)."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from _t13 import conn_for, corpus_workspace, seed_sync  # noqa: F401  (sets sys.path for _db)
from _db import GOLDEN
from conftest import DATABASE_URL_TEST, SECRET, needs_db
from scribe_ingest.embedding import FakeEmbedder
from scribe_intel.retrieval import registry
from scribe_intel.retrieval.profiles import SEED_PROFILES
from scribe_intel.retrieval.rerank import VoyageReranker

pytestmark = needs_db
NAMES = [p.name for p in SEED_PROFILES]
RERANK_NAMES = [p.name for p in SEED_PROFILES if p.config.rerank]
SEED_IDS = {p.name: f"a0000000-0000-4000-8000-00000000000{i}" for i, p in enumerate(SEED_PROFILES, 1)}
Q = "ERR_AUTH_403 INV-2047 error code ticket"


def token(wid: str, scope: str = "retrieve") -> str:
    now = int(time.time())
    return jwt.encode({"sub": "user_1", "wid": wid, "scope": scope, "aud": "scribe-intel", "iat": now, "exp": now + 60},
                      SECRET, algorithm="HS256")


def auth(wid):
    return {"authorization": f"Bearer {token(wid)}"}


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
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c
    await db.close_pool()
    get_settings.cache_clear()


@pytest.fixture()
def fake_rerank(monkeypatch):
    monkeypatch.setenv("SCRIBE_RERANKER", "fake")
    monkeypatch.setenv("SCRIBE_ALLOW_FAKE_RERANKER", "1")


@pytest.fixture()
async def conn():
    c = await conn_for(DATABASE_URL_TEST)
    yield c
    await c.close()


@pytest.fixture()
def http_calls(monkeypatch):
    """Record (and refuse) any REAL outbound HTTP request; the in-process ASGI client is unaffected."""
    calls: list[str] = []

    async def refuse(self, request):
        calls.append(str(request.url))
        raise AssertionError("no network allowed in tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    return calls


async def post(client, wid, profile, query=Q, **kw):
    return await client.post("/api/py/retrieve", json={"query": query, "profile": profile, **kw}, headers=auth(wid))


def test_all_five_profiles_are_registered():
    assert set(NAMES) <= set(registry.profile_names())
    assert len(NAMES) == 5 and {"dense-exact", "dense-ann", "lexical-v1", "legacy-dense"} <= set(registry.profile_names())


@pytest.mark.parametrize("profile", NAMES)
async def test_every_profile_is_served(client, seeded, fake_rerank, profile):
    r = await post(client, seeded["wa"], profile)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["profile"] == profile and j["degraded"] == [] and j["trace_id"] and j["candidates"]
    assert "context" in j and j["context"]["used_tokens"] <= j["context"]["budget_tokens"] == 6000
    assert j["meta"]["profile_config_hash"] and j["meta"]["used_tokens"] == j["context"]["used_tokens"]
    assert {"fusion", "pack", "total", "request_total"} <= set(j["stage_ms"])
    assert all(isinstance(v, int) for v in j["stage_ms"].values())
    assert [c["rank"] for c in j["candidates"]] == list(range(1, len(j["candidates"]) + 1))


async def test_hybrid_rrf_fuses_both_lists(client, seeded):
    r = await post(client, seeded["wa"], "hybrid-rrf", limit=50)
    j = r.json()
    assert r.status_code == 200 and j["degraded"] == []
    cands = j["candidates"]
    assert [c["fusion_rank"] for c in cands] == list(range(1, len(cands) + 1))
    assert any(c["dense_rank"] and c["lexical_rank"] for c in cands)
    assert all(c["rerank_rank"] is None for c in cands) and j["meta"]["reranker"] is None
    assert j["meta"]["dense_mode"] == "exact" and j["counts"]["dense"] > 0 and j["counts"]["lexical"] > 0


async def test_single_retriever_profiles_use_one_list(client, seeded):
    d = (await post(client, seeded["wa"], "dense-only")).json()
    assert d["candidates"] and all(c["dense_rank"] and c["lexical_rank"] is None for c in d["candidates"])
    lx = (await post(client, seeded["wa"], "lexical-only")).json()
    assert lx["candidates"] and all(c["lexical_rank"] and c["dense_rank"] is None for c in lx["candidates"])
    assert "dense_mode" not in lx["meta"] and "embed" not in lx["stage_ms"]


@pytest.mark.parametrize("profile", RERANK_NAMES)
async def test_fake_reranker_sets_rerank_fields_and_is_stamped(client, seeded, fake_rerank, profile):
    j = (await post(client, seeded["wa"], profile, limit=50)).json()
    assert j["degraded"] == []
    assert j["meta"]["reranker_is_fake"] is True and j["meta"]["reranker"] == "fake-reranker"
    head = [c for c in j["candidates"] if c["rerank_rank"] is not None]
    assert head and [c["rerank_rank"] for c in head] == list(range(1, len(head) + 1))
    assert all(c["rerank_score"] is not None for c in head)
    scores = [c["rerank_score"] for c in head]
    assert scores == sorted(scores, reverse=True) and j["meta"]["top_rerank_score"] == scores[0]
    assert len(head) <= 20 and j["counts"]["reranked"] == len(head)


async def test_non_rerank_profiles_are_not_stamped_fake(client, seeded, fake_rerank):
    j = (await post(client, seeded["wa"], "hybrid-rrf")).json()
    assert j["meta"]["reranker_is_fake"] is False and j["meta"]["reranker"] is None


def assert_fused_fallback(j):
    assert "rerank_unavailable" in j["degraded"]
    cands = j["candidates"]
    assert cands and all(c["rerank_rank"] is None and c["rerank_score"] is None for c in cands)
    assert [c["fusion_rank"] for c in cands] == list(range(1, len(cands) + 1))
    assert j["meta"]["top_rerank_score"] is None
    assert j["context"]["used_tokens"] <= 6000 and j["context"]["entries"]
    # degraded context: only the first rerank_top_n (20) fused candidates are eligible, never the tail
    rank_of = {c["chunk_id"]: c["fusion_rank"] for c in j["candidates"]}
    ids = [x for e in j["context"]["entries"] for x in (e["chunk_id"], *e["merged_chunk_ids"])]
    assert ids and all(x not in rank_of or rank_of[x] <= 20 for x in ids)


@pytest.mark.parametrize("profile", RERANK_NAMES)
async def test_reranker_none_degrades_to_fused_order(client, seeded, monkeypatch, profile):
    monkeypatch.setenv("SCRIBE_RERANKER", "none")
    r = await post(client, seeded["wa"], profile)
    assert r.status_code == 200, r.text
    assert_fused_fallback(r.json())


@pytest.mark.parametrize("status,code", [(429, "RATE_LIMITED"), (500, "TRANSIENT_PROVIDER")])
async def test_voyage_errors_degrade_not_fail(client, seeded, conn, monkeypatch, caplog, status, code):
    from scribe_intel import retrieve_routes

    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(status, json={"detail": "nope"})

    async def nosleep(_):
        return None

    monkeypatch.setattr(retrieve_routes, "make_reranker_for", lambda dsn: lambda model: VoyageReranker(
        api_key="test-key", model=model, transport=httpx.MockTransport(handler), sleep=nosleep))
    with caplog.at_level("WARNING", logger="scribe.retrieve"):
        r = await post(client, seeded["wa"], "hybrid-rrf-rerank")
    assert r.status_code == 200, r.text
    assert_fused_fallback(r.json())
    assert len(seen) > 1  # retried, then gave up
    assert r.json()["meta"]["rerank_error"] == code
    cur = await conn.execute("select counts from retrieval_traces where id=%s", (r.json()["trace_id"],))
    assert (await cur.fetchone())[0]["rerank_error"] == code
    assert sum(code in m.getMessage() for m in caplog.records if m.name == "scribe.retrieve") == 1
    assert "test-key" not in caplog.text and Q not in caplog.text
    assert r.json()["meta"]["reranker"] == "rerank-2.5" and r.json()["meta"]["reranker_is_fake"] is False


async def test_voyage_without_key_degrades_and_makes_no_request(client, seeded, monkeypatch, http_calls):
    monkeypatch.setenv("SCRIBE_RERANKER", "voyage")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    r = await post(client, seeded["wa"], "hybrid-rrf-rerank")
    assert r.status_code == 200, r.text
    assert_fused_fallback(r.json())
    assert http_calls == [] and r.json()["meta"]["rerank_error"] == "AUTH"
    # default (env unset) is voyage as well
    monkeypatch.delenv("SCRIBE_RERANKER")
    r = await post(client, seeded["wa"], "dense-rerank")
    assert r.status_code == 200 and "rerank_unavailable" in r.json()["degraded"] and http_calls == []


async def test_fake_reranker_refused_without_allow_flag(client, seeded, monkeypatch):
    monkeypatch.setenv("SCRIBE_RERANKER", "fake")
    monkeypatch.delenv("SCRIBE_ALLOW_FAKE_RERANKER", raising=False)
    r = await post(client, seeded["wa"], "hybrid-rrf-rerank")
    assert r.status_code == 500 and r.json()["error_code"] == "FAKE_RERANKER_REFUSED"
    monkeypatch.setenv("SCRIBE_ALLOW_FAKE_RERANKER", "0")
    assert (await post(client, seeded["wa"], "hybrid-rrf-rerank")).json()["error_code"] == "FAKE_RERANKER_REFUSED"


def test_make_reranker_for_requires_test_database_and_valid_kind():
    from scribe_intel.retrieve_routes import FakeRerankerRefused, make_reranker_for

    env = {"SCRIBE_RERANKER": "fake", "SCRIBE_ALLOW_FAKE_RERANKER": "1"}
    with pytest.raises(FakeRerankerRefused):
        make_reranker_for("postgresql://u:p@h/production", env)
    assert make_reranker_for("postgresql://u:p@h/scribe_test", env)("m").is_fake is True
    assert make_reranker_for("postgresql://u:p@h/production", {"SCRIBE_RERANKER": "none"})("m") is None
    assert make_reranker_for("postgresql://u:p@h/production", {})("rerank-2.5").model == "rerank-2.5"
    with pytest.raises(ValueError):
        make_reranker_for("postgresql://u:p@h/x", {"SCRIBE_RERANKER": "cohere"})


async def parent_text(conn, parent_id):
    cur = await conn.execute("select content from chunks where id=%s and parent_id is null", (parent_id,))
    return (await cur.fetchone())[0]


async def test_default_profile_context_expands_parents(client, seeded, fake_rerank, conn):
    r = await post(client, seeded["wa"], "hybrid-rrf-rerank")
    j = r.json()
    ctx = j["context"]
    assert r.status_code == 200 and j["degraded"] == []
    assert 0 < ctx["used_tokens"] <= 6000 and ctx["budget_tokens"] == 6000
    assert ctx["used_tokens"] == sum(e["token_count"] for e in ctx["entries"])
    expanded = [e for e in ctx["entries"] if e["expanded_from_parent"]]
    assert expanded
    cands = {c["chunk_id"]: c for c in (await post(client, seeded["wa"], "hybrid-rrf-rerank", limit=50)).json()["candidates"]}
    checked = 0
    for e in expanded:
        assert e["text"] == await parent_text(conn, e["parent_id"])
        assert e["spans"] and e["doc_name"] and e["source_id"] and e["source_version_id"]
        child = cands.get(e["chunk_id"])
        if child is not None:  # the child's own spans are kept (plus merged siblings') for citation
            assert child["spans"] and {json.dumps(x, sort_keys=True) for x in child["spans"]} <= {
                json.dumps(x, sort_keys=True) for x in e["spans"]}
            checked += 1
    assert checked >= 1, "no expanded entry could be matched to its child candidate"
    assert {"chunk_id", "parent_id", "text", "heading_text", "doc_name", "source_id", "source_version_id", "token_count",
            "rerank_score", "expanded_from_parent", "merged_chunk_ids", "duplicates", "spans"} <= set(ctx["entries"][0])
    assert isinstance(ctx["dropped"], list)
    # the candidates list contract is unchanged: first `limit` of ranked
    assert len(j["candidates"]) <= 10


async def test_profile_without_expansion_keeps_child_text(client, seeded, fake_rerank):
    ctx = (await post(client, seeded["wa"], "dense-rerank")).json()["context"]
    assert ctx["entries"] and not any(e["expanded_from_parent"] for e in ctx["entries"])


async def test_parent_prefetch_failure_degrades(client, seeded, fake_rerank, monkeypatch):
    from scribe_intel.retrieval.strategies import hybrid

    async def boom(*a, **k):
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(hybrid, "load_parents", boom)
    r = await post(client, seeded["wa"], "hybrid-rrf-rerank")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["degraded"] == ["parent_expansion_unavailable"]
    assert j["context"]["entries"] and not any(e["expanded_from_parent"] for e in j["context"]["entries"])


async def test_parent_sql_failure_inside_snapshot_degrades(client, seeded, fake_rerank, monkeypatch, conn):
    """The parent statement itself fails inside the snapshot tx (aborting it); the already-fetched
    children are still returned and the request degrades."""
    from scribe_intel.retrieval import parents

    monkeypatch.setattr(parents, "SCOPE_JOINS", "join no_such_table_for_test nst on true")
    r = await post(client, seeded["wa"], "hybrid-rrf-rerank")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["degraded"] == ["parent_expansion_unavailable"]
    assert j["candidates"] and j["context"]["entries"]
    assert not any(e["expanded_from_parent"] for e in j["context"]["entries"])
    assert j["counts"]["dense"] > 0 and j["counts"]["lexical"] > 0
    cur = await conn.execute("select degraded from retrieval_traces where id=%s", (j["trace_id"],))
    assert (await cur.fetchone())[0] == "parent_expansion_unavailable"


async def test_cache_miss_is_loud_not_degraded(client, seeded, monkeypatch):
    from scribe_intel import retrieve_routes
    from scribe_intel.retrieval.rerank import CacheMiss

    class Replay:
        is_fake = False
        model = "rerank-2.5"

        async def rerank(self, query, docs, top_n):
            raise CacheMiss("replay miss")

    monkeypatch.setattr(retrieve_routes, "make_reranker_for", lambda dsn: lambda model: Replay())
    r = await post(client, seeded["wa"], "hybrid-rrf-rerank")
    j = r.json()
    assert r.status_code == 500 and j["error_code"] == "RERANK_CACHE_MISS" and j["cause"] == "CacheMiss"
    assert j["retryable"] is False and "candidates" not in j


@pytest.mark.parametrize("profile", NAMES)
async def test_trace_profile_id_and_selected_flags(client, seeded, fake_rerank, conn, profile):
    j = (await post(client, seeded["wa"], profile, limit=3)).json()
    cur = await conn.execute("select t.profile_id, p.name, t.config_hash, t.counts from retrieval_traces t "
                             "left join retrieval_profiles p on p.id = t.profile_id where t.id=%s", (j["trace_id"],))
    pid, pname, cfg_hash, counts = await cur.fetchone()
    assert str(pid) == SEED_IDS[profile] and pname == profile and cfg_hash
    assert counts["profile"] == profile and counts["returned"] == len(j["candidates"])
    in_ctx = set()
    for e in j["context"]["entries"]:
        in_ctx.add(e["chunk_id"])
        in_ctx.update(e["merged_chunk_ids"])
        in_ctx.update(e["duplicates"])
    cur = await conn.execute("select chunk_id, selected from retrieval_candidates where trace_id=%s", (j["trace_id"],))
    rows = await cur.fetchall()
    assert rows and {str(c) for c, s in rows if s} == in_ctx
    assert len(rows) >= len(j["candidates"])  # whole ranked list is traced, not only the returned page


async def test_trace_config_hash_pins_profile_config_and_request(client, seeded, conn):
    async def cfg(profile, **kw):
        j = (await post(client, seeded["wa"], profile, **kw)).json()
        return (await (await conn.execute("select config_hash from retrieval_traces where id=%s", (j["trace_id"],))).fetchone())[0]

    a, b = await cfg("hybrid-rrf"), await cfg("hybrid-rrf")
    assert a == b and a != await cfg("hybrid-rrf", limit=5) and a != await cfg("dense-only")
    p = next(x for x in SEED_PROFILES if x.name == "hybrid-rrf")
    expected = hashlib.sha256(json.dumps({"profile": p.config_hash, "limit": 10, "source_ids": None},
                                         sort_keys=True).encode()).hexdigest()
    assert a == expected


async def test_ok_and_failed_traces_share_config_hash_and_profile_link(client, seeded, conn, monkeypatch):
    from scribe_intel import retrieve_routes

    async def trace(j):
        cur = await conn.execute("select config_hash, status, profile_id from retrieval_traces where id=%s", (j["trace_id"],))
        return await cur.fetchone()

    kw = {"limit": 7, "source_ids": [seeded["a_ids"]["api-errors.md"]]}
    ok = await trace((await post(client, seeded["wa"], "hybrid-rrf", **kw)).json())
    monkeypatch.setattr(retrieve_routes, "make_embedder_for", lambda dsn: lambda dims: FakeEmbedder(dims, fail_after=0))
    r = await post(client, seeded["wa"], "hybrid-rrf", **kw)
    assert r.status_code == 503
    failed = await trace(r.json())
    assert (ok[1], failed[1]) == ("ok", "failed")
    assert ok[0] == failed[0] and str(ok[2]) == str(failed[2]) == SEED_IDS["hybrid-rrf"]


async def test_profile_id_links_by_name_and_config_hash(client, seeded, conn, monkeypatch):
    """A same-name row whose config hash differs from the code's must never be linked."""
    from scribe_intel import retrieve_routes
    from scribe_intel.retrieval.profiles import BY_NAME, ProfileConfig, RetrievalProfile

    drifted = RetrievalProfile(name="hybrid-rrf", description="x",
                               config=ProfileConfig(dense_top_k=41, lexical_top_k=40))
    assert drifted.config_hash != BY_NAME["hybrid-rrf"].config_hash
    monkeypatch.setattr(retrieve_routes, "BY_NAME", {**BY_NAME, "hybrid-rrf": drifted})
    j = (await post(client, seeded["wa"], "hybrid-rrf")).json()
    cur = await conn.execute("select profile_id from retrieval_traces where id=%s", (j["trace_id"],))
    assert j["degraded"] == [] and (await cur.fetchone())[0] is None
    # the unmodified mapping links the seeded row again
    monkeypatch.setattr(retrieve_routes, "BY_NAME", BY_NAME)
    j = (await post(client, seeded["wa"], "hybrid-rrf")).json()
    cur = await conn.execute("select profile_id::text from retrieval_traces where id=%s", (j["trace_id"],))
    assert (await cur.fetchone())[0] == SEED_IDS["hybrid-rrf"]


async def test_t13_strategies_keep_null_profile_and_no_context(client, seeded, conn):
    r = await post(client, seeded["wa"], "lexical-v1", query="ERR_AUTH_403")
    j = r.json()
    assert r.status_code == 200 and "context" not in j
    cur = await conn.execute("select profile_id from retrieval_traces where id=%s", (j["trace_id"],))
    assert (await cur.fetchone())[0] is None
    cur = await conn.execute("select selected, count(*) from retrieval_candidates where trace_id=%s group by 1", (j["trace_id"],))
    flags = dict(await cur.fetchall())
    assert flags.get(True) == min(10, sum(flags.values()))


@pytest.mark.parametrize("profile", NAMES)
async def test_workspace_comes_from_token_not_body(client, seeded, fake_rerank, conn, profile):
    for token_ws, other_ws, own, other in (("wb", "wa", "b_ids", "a_ids"), ("wa", "wb", "a_ids", "b_ids")):
        r = await post(client, seeded[token_ws], profile, query="ERR_AUTH_403",
                       workspace_id=seeded[other_ws],
                       source_ids=[seeded[other]["api-errors.md"], seeded[own]["api-errors.md"]])
        assert r.status_code == 200, r.text
        j = r.json()
        own_sources = set(seeded[own].values())
        assert j["candidates"]
        assert {c["source_id"] for c in j["candidates"]} == {seeded[own]["api-errors.md"]}
        assert {e["source_id"] for e in j["context"]["entries"]} <= own_sources
        ids = [uuid.UUID(x) for e in j["context"]["entries"] for x in (e["chunk_id"], e["parent_id"]) if x]
        cur = await conn.execute("select distinct workspace_id from chunks where id = any(%s)", (ids,))
        assert [str(x[0]) for x in await cur.fetchall()] == [seeded[token_ws]]


async def test_lexical_only_works_when_the_embedder_fails(client, seeded, monkeypatch):
    from scribe_intel import retrieve_routes

    monkeypatch.setattr(retrieve_routes, "make_embedder_for", lambda dsn: lambda dims: FakeEmbedder(dims, fail_after=0))
    r = await post(client, seeded["wa"], "lexical-only", query="ERR_AUTH_403")
    assert r.status_code == 200 and r.json()["degraded"] == []
    top = r.json()["candidates"][0]
    assert top["doc_name"] == "api-errors.md" and "ERR_AUTH_403" in top["content"]
    # profiles that use the dense retriever fail loudly (no silent lexical-only fallback)
    for profile in ("dense-only", "hybrid-rrf", "hybrid-rrf-rerank"):
        r = await post(client, seeded["wa"], profile, query="ERR_AUTH_403")
        assert r.status_code == 503 and r.json()["error_code"] == "RETRIEVAL_FAILED", profile


async def test_lexical_only_never_calls_the_embedder(client, seeded, monkeypatch):
    from scribe_intel import retrieve_routes

    calls = []
    real = retrieve_routes.make_embedder_for

    def factory(dsn):
        inner = real(dsn)

        def embedder_for(dims):
            e = inner(dims)
            orig = e.embed_queries

            async def spy(texts):
                calls.append(texts)
                return await orig(texts)

            e.embed_queries = spy
            return e
        return embedder_for

    monkeypatch.setattr(retrieve_routes, "make_embedder_for", factory)
    assert (await post(client, seeded["wa"], "lexical-only", query="annual leave")).status_code == 200
    assert calls == []
    assert (await post(client, seeded["wa"], "dense-only", query="annual leave")).status_code == 200
    assert len(calls) == 1


def _golden_questions():
    return [json.loads(line)["question"] for line in GOLDEN.read_text().splitlines() if line.strip()]


async def test_every_golden_question_runs_through_hybrid_rrf_rerank(client, corpus, fake_rerank):
    """Smoke only: fake vectors / fake reranker carry no semantics, so NO quality is asserted or reported."""
    qs = _golden_questions()
    assert len(qs) >= 100
    for q in qs:
        r = await post(client, corpus["wid"], "hybrid-rrf-rerank", query=q, limit=20)
        assert r.status_code == 200, (q, r.text)
        j = r.json()
        assert j["degraded"] == [], q
        assert 0 < len(j["candidates"]) <= 20
        ctx = j["context"]
        assert ctx["used_tokens"] <= ctx["budget_tokens"] == 6000 and ctx["entries"]
        assert j["meta"]["reranker_is_fake"] is True

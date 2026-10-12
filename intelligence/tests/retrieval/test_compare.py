"""T29: POST /compare runs 2-5 profiles in ONE snapshot. Offline: fake embedder and fake/mocked rerankers only (every
fake is stamped in the response). Plus the pure rank-movement / alignment math."""
from __future__ import annotations

import logging
import time
import uuid

import httpx
import jwt
import pytest
from _db import new_workspace
from _t13 import API, HR, STEM, conn_for, ingest, seed_sync
from conftest import DATABASE_URL_TEST, SECRET, needs_db
from scribe_ingest.embedding import FakeEmbedder
from scribe_intel import compare_routes
from scribe_intel.retrieval import compare as cmp
from scribe_intel.retrieval.profiles import SEED_PROFILES
from scribe_intel.retrieval.rerank import FakeReranker, RerankError

NAMES = [p.name for p in SEED_PROFILES]
Q = "ERR_AUTH_403 INV-2047 error code ticket"


def auth(wid: str, scope: str = "compare") -> dict:
    now = int(time.time())
    t = jwt.encode({"sub": "user_1", "wid": wid, "scope": scope, "aud": "scribe-intel", "iat": now, "exp": now + 60},
                   SECRET, algorithm="HS256")
    return {"authorization": f"Bearer {t}"}


# ======================================================================================== pure math (no database)

def test_rank_movement_math():
    m = cmp.rank_movement
    assert m(3, 1) == {"direction": "up", "delta": 2, "places": 2}      # delta = baseline - rank: positive = up
    assert m(1, 4) == {"direction": "down", "delta": -3, "places": 3}
    assert m(2, 2) == {"direction": "same", "delta": 0, "places": 0}
    assert m(None, 5) == {"direction": "new", "delta": None, "places": None}
    assert m(5, None) == {"direction": "dropped", "delta": None, "places": None}
    assert m(None, None) == {"direction": "absent", "delta": None, "places": None}


def _r(cid, rank, name="d.md"):
    return {"chunk_id": cid, "rank": rank, "doc_name": name, "heading_text": None, "snippet": cid, "source_id": 1}


def test_alignment_rows_summary_and_ordering():
    lists = {
        "base": [_r("a", 1), _r("b", 2), _r("c", 3)],
        "p2": [_r("c", 1), _r("a", 2), _r("d", 3)],          # c up 2, a down 1, b dropped, d new
        "p3": [_r("a", 1), _r("b", 2), _r("c", 3)],          # identical to the baseline
    }
    al = cmp.build_alignment(["base", "p2", "p3"], lists)
    assert al["baseline"] == "base"
    assert [r["chunk_id"] for r in al["rows"]] == ["a", "b", "c", "d"]   # baseline order first, then the rest
    by = {r["chunk_id"]: r for r in al["rows"]}
    assert by["c"]["ranks"] == {"base": 3, "p2": 1, "p3": 3}
    assert by["c"]["movement"]["p2"] == {"direction": "up", "delta": 2, "places": 2}
    assert by["a"]["movement"]["p2"]["direction"] == "down" and by["a"]["movement"]["p2"]["delta"] == -1
    assert by["b"]["movement"]["p2"]["direction"] == "dropped" and by["b"]["ranks"]["p2"] is None
    assert by["d"]["movement"]["p2"]["direction"] == "new" and by["d"]["ranks"]["base"] is None
    assert by["d"]["movement"]["p3"]["direction"] == "new" or by["d"]["movement"]["p3"]["direction"] == "absent"
    assert by["c"]["rank_spread"] == 2 and by["c"]["present_in"] == 3 and by["d"]["present_in"] == 1
    assert all(r["movement"]["base"]["direction"] in ("same", "absent") for r in al["rows"])
    s = al["summary"]
    assert s["base"]["overlap_with_baseline"] == 3 and s["base"]["jaccard_with_baseline"] == 1.0
    assert (s["p2"]["up"], s["p2"]["down"], s["p2"]["same"], s["p2"]["new"], s["p2"]["dropped"]) == (1, 1, 0, 1, 1)
    assert s["p2"]["overlap_with_baseline"] == 2 and s["p2"]["jaccard_with_baseline"] == 0.5   # 2 / |{a,b,c,d}|
    assert s["p3"]["same"] == 3 and s["p3"]["jaccard_with_baseline"] == 1.0


def test_alignment_with_an_empty_baseline_and_empty_lists():
    al = cmp.build_alignment(["x", "y"], {"x": [], "y": [_r("a", 1)]})
    assert [r["movement"]["y"]["direction"] for r in al["rows"]] == ["new"]
    empty = cmp.build_alignment(["x", "y"], {"x": [], "y": []})
    assert empty["rows"] == [] and empty["summary"]["x"]["jaccard_with_baseline"] == 1.0


def test_snippet_collapses_whitespace_and_truncates():
    assert cmp.snippet("a  b\n\nc") == "a b c"
    s = cmp.snippet("word " * 200, 50)
    assert len(s) <= 50 and s.endswith("…")


# ============================================================================================= endpoint (database)

@pytest.fixture(scope="module")
def seeded():
    if not DATABASE_URL_TEST:
        pytest.skip("DATABASE_URL_TEST not set")
    return seed_sync(DATABASE_URL_TEST)


@pytest.fixture()
async def client(monkeypatch):
    from scribe_intel import db
    from scribe_intel.app import create_app
    from scribe_intel.config import get_settings

    for k, v in dict(DATABASE_URL=DATABASE_URL_TEST or "", SCRIBE_EMBEDDER="fake", SCRIBE_ALLOW_FAKE_EMBEDDINGS="1",
                     SCRIBE_RERANKER="fake", SCRIBE_ALLOW_FAKE_RERANKER="1").items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("SCRIBE_PRICE_EMBED_PER_MTOK_USD", raising=False)
    monkeypatch.delenv("SCRIBE_PRICE_RERANK_PER_MTOK_USD", raising=False)
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c
    await db.close_pool()
    get_settings.cache_clear()


@pytest.fixture()
def no_network(monkeypatch):
    calls: list[str] = []

    async def refuse(self, request):
        calls.append(str(request.url))
        raise AssertionError("no network allowed in tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    return calls


async def post(client, tenant, profiles=None, query=Q, scope="compare", **kw):
    return await client.post("/api/py/compare", json={"query": query, "profiles": profiles or NAMES, **kw},
                             headers=auth(tenant, scope))


# ---------------------------------------------------------------------------------------------- validation

@needs_db
@pytest.mark.parametrize("profiles", [[], ["hybrid-rrf"], NAMES + ["hybrid-rrf"], ["hybrid-rrf", "hybrid-rrf"]])
async def test_profile_count_outside_2_to_5_and_duplicates_are_422(client, seeded, profiles):
    r = await client.post("/compare", json={"query": Q, "profiles": profiles}, headers=auth(seeded["wa"]))
    assert r.status_code == 422, r.text


@needs_db
@pytest.mark.parametrize("body", [{"query": "  "}, {"query": ""}, {"query": "x", "limit": 0}, {"query": "x", "limit": 51},
                                  {"query": "x", "profiles": "hybrid-rrf"}, {"profiles": ["hybrid-rrf", "dense-only"]}])
async def test_other_invalid_bodies_are_422(client, seeded, body):
    payload = {"profiles": ["hybrid-rrf", "dense-only"], **body}
    if "query" not in body and "profiles" in body:
        payload.pop("query", None)
    r = await client.post("/compare", json=payload, headers=auth(seeded["wa"]))
    assert r.status_code == 422, r.text


@needs_db
async def test_two_and_five_profiles_are_accepted(client, seeded):
    assert (await post(client, seeded["wa"], NAMES[:2])).status_code == 200
    assert (await post(client, seeded["wa"], NAMES)).status_code == 200


@needs_db
@pytest.mark.parametrize("bad", ["nope", "dense-exact", "legacy-dense", "lexical-v1", "HYBRID-RRF"])
async def test_unknown_profile_is_404_and_lists_the_comparable_ones(client, seeded, bad):
    r = await post(client, seeded["wa"], ["hybrid-rrf", bad])
    assert r.status_code == 404, r.text
    j = r.json()
    assert j["error_code"] == "UNKNOWN_PROFILE" and j["unknown"] == [bad] and sorted(j["profiles"]) == sorted(NAMES)


@needs_db
async def test_wrong_scope_is_403_and_a_body_workspace_is_ignored(client, seeded):
    assert (await post(client, seeded["wa"], scope="retrieve")).status_code == 403
    r = await post(client, seeded["wa"], ["hybrid-rrf", "dense-only"], query="platypus sanctuary expands spring",
                   workspace_id=seeded["wb"], wid=seeded["wb"])
    assert r.status_code == 200
    names = {x["doc_name"] for p in r.json()["profile_results"] for x in p["results"]}
    assert "b-only.md" not in names  # workspace A's token: B's document never appears


# ---------------------------------------------------------------------------------------------- the response

@needs_db
async def test_response_shape_for_all_five_profiles(client, seeded, no_network):
    r = await post(client, seeded["wa"], limit=8)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["profiles"] == NAMES and [p["profile"] for p in j["profile_results"]] == NAMES
    assert len(j["config_hash"]) == 64 and j["snapshot"]["id"] and j["snapshot"]["read_only"] is True
    assert j["snapshot"]["isolation"] == "repeatable read" and j["snapshot"]["shared_by"] == NAMES
    seed = {p.name: p for p in SEED_PROFILES}
    for p in j["profile_results"]:
        assert p["config_hash"] == seed[p["profile"]].config_hash and p["config"] == seed[p["profile"]].config_json
        assert p["degraded"] == [] and p["trace_id"] and 0 < len(p["results"]) <= 8
        assert [x["rank"] for x in p["results"]] == list(range(1, len(p["results"]) + 1))
        assert all(isinstance(v, int) for v in p["stage_ms"].values()) and "read_in_snapshot" in p["stage_ms"] and "fusion" in p["stage_ms"]
        assert p["counts"]["returned"] == len(p["results"])
        assert set(p["cost"]) >= {"estimated", "note", "rerank_documents", "rerank_tokens", "usd"}
        assert p["cost"]["estimated"] is True and p["cost"]["note"] == "estimated; 0 for fake adapters"
        assert all("score" not in x for x in p["results"])  # per-ranker scores only, never one combined score (INV-10)
    by = {p["profile"]: p for p in j["profile_results"]}
    # which ranker contributed which fields
    assert all(x["dense_rank"] and x["lexical_rank"] is None and x["rerank_rank"] is None for x in by["dense-only"]["results"])
    assert all(x["lexical_rank"] and x["dense_rank"] is None for x in by["lexical-only"]["results"])
    assert any(x["dense_rank"] and x["lexical_rank"] for x in by["hybrid-rrf"]["results"])
    assert all(x["rerank_rank"] is not None for x in by["hybrid-rrf-rerank"]["results"][:3])
    assert by["hybrid-rrf-rerank"]["meta"]["reranker_is_fake"] is True
    # fusion is derived from ranks only (INV-10): 1/(60+dense_rank) + 1/(60+lexical_rank), not from any score
    for x in by["hybrid-rrf"]["results"]:
        want = sum(1 / (60 + rk) for rk in (x["dense_rank"], x["lexical_rank"]) if rk)
        assert x["fusion_score"] == pytest.approx(want, rel=1e-9)
    assert no_network == []
    # fakes cost nothing, and say so
    assert j["cost"]["usd"] == 0.0 and j["cost"]["embed_usd"] == 0.0 and "0 for fake" in j["cost"]["note"]
    assert j["shared"]["embedding"]["is_fake"] is True and j["shared"]["embedding"]["calls"] == 1
    assert by["hybrid-rrf-rerank"]["cost"]["rerank_documents"] > 0 and by["hybrid-rrf-rerank"]["cost"]["usd"] == 0.0
    assert by["dense-only"]["cost"]["rerank_documents"] == 0


@needs_db
async def test_alignment_matches_the_per_profile_rankings(client, seeded):
    j = (await post(client, seeded["wa"], ["dense-only", "lexical-only", "hybrid-rrf-rerank"], limit=10)).json()
    al = j["alignment"]
    assert al["baseline"] == "dense-only"
    ranks = {p["profile"]: {x["chunk_id"]: x["rank"] for x in p["results"]} for p in j["profile_results"]}
    assert {r["chunk_id"] for r in al["rows"]} == set().union(*(set(v) for v in ranks.values()))
    for row in al["rows"]:
        for name in j["profiles"]:
            assert row["ranks"][name] == ranks[name].get(row["chunk_id"])
            assert row["movement"][name] == cmp.rank_movement(ranks["dense-only"].get(row["chunk_id"]), ranks[name].get(row["chunk_id"]))
    base_order = [r["ranks"]["dense-only"] for r in al["rows"] if r["ranks"]["dense-only"] is not None]
    assert base_order == sorted(base_order)


@needs_db
async def test_embedding_is_computed_once_for_all_dense_profiles(client, seeded, monkeypatch):
    calls: list[list[str]] = []
    orig = FakeEmbedder.embed_queries

    async def spy(self, texts):
        calls.append(list(texts))
        return await orig(self, texts)

    monkeypatch.setattr(FakeEmbedder, "embed_queries", spy)
    j = (await post(client, seeded["wa"])).json()
    assert len(calls) == 1 and calls[0] == [Q]
    assert j["shared"]["embedding"]["shared_by"] == [p for p in NAMES if p != "lexical-only"]
    calls.clear()
    j = (await post(client, seeded["wa"], ["lexical-only", "hybrid-rrf"])).json()
    assert len(calls) == 1 and j["shared"]["embedding"]["shared_by"] == ["hybrid-rrf"]


@needs_db
async def test_each_profile_gets_a_marked_trace_and_no_evidence_bundle(client, seeded):
    import psycopg

    j = (await post(client, seeded["wa"], ["dense-only", "hybrid-rrf", "hybrid-rrf-rerank"])).json()
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        rows = c.execute("select id::text, counts->>'compare', counts->>'compare_id', counts->>'profile', workspace_id::text "
                         "from retrieval_traces where id = any(%s::uuid[])", ([p["trace_id"] for p in j["profile_results"]],)).fetchall()
        assert len(rows) == 3
        assert {r[1] for r in rows} == {"true"} and {r[2] for r in rows} == {j["compare_id"]}
        assert {r[3] for r in rows} == {"dense-only", "hybrid-rrf", "hybrid-rrf-rerank"} and {r[4] for r in rows} == {seeded["wa"]}
        n = c.execute("select count(*) from evidence_bundles where trace_id = any(%s::uuid[])",
                      ([p["trace_id"] for p in j["profile_results"]],)).fetchone()[0]
        assert n == 0  # a comparison never mints E-labels


# ---------------------------------------------------------------------------------------------- snapshot (D12)

@needs_db
async def test_all_profiles_run_in_one_snapshot_and_ignore_a_concurrent_delete(client, monkeypatch):
    """Profile 1 reads, then ANOTHER connection soft-deletes the source and commits, then profiles 2..5 read. They must
    still see the source (one REPEATABLE READ snapshot). A fresh comparison afterwards no longer returns it."""
    conn = await conn_for(DATABASE_URL_TEST)
    try:
        wid = await new_workspace(conn)
        emb = FakeEmbedder()
        f_api = await ingest(conn, wid, "api-errors.md", API, emb)
        await ingest(conn, wid, "hr.md", HR, emb)
        await ingest(conn, wid, "stem.md", STEM, emb)
        entered = []
        orig_tx = compare_routes.snapshot_tx

        def counting_tx(pool, workspace_id):
            entered.append(workspace_id)
            return orig_tx(pool, workspace_id)

        monkeypatch.setattr(compare_routes, "snapshot_tx", counting_tx)
        orig_read = compare_routes.read_in_snapshot
        done: list[str] = []

        async def read_then_delete(conn_, workspace_id, query, p, prof, **kw):
            res = await orig_read(conn_, workspace_id, query, p, prof, **kw)
            done.append(p.name)
            if len(done) == 1:  # after the FIRST profile's read, outside the snapshot
                other = await conn_for(DATABASE_URL_TEST)
                try:
                    await other.execute("update files set deleted_at = now() where id=%s", (f_api["id"],))
                finally:
                    await other.close()
            return res

        monkeypatch.setattr(compare_routes, "read_in_snapshot", read_then_delete)
        j = (await post(client, wid, NAMES, query=Q, limit=20)).json()
        assert len(entered) == 1 and len(done) == 5            # one transaction for the five profiles
        for p in j["profile_results"]:
            ids = {x["source_id"] for x in p["results"]}
            assert f_api["id"] in ids, f"{p['profile']} lost the source mid-comparison (not one snapshot)"
        # the very next comparison opens a new snapshot: the deleted source is gone from all five profiles
        monkeypatch.setattr(compare_routes, "read_in_snapshot", orig_read)
        j2 = (await post(client, wid, NAMES, query=Q, limit=20)).json()
        assert j2["snapshot"]["id"] != j["snapshot"]["id"]
        for p in j2["profile_results"]:
            assert f_api["id"] not in {x["source_id"] for x in p["results"]}, p["profile"]
    finally:
        await conn.close()


@needs_db
async def test_snapshot_id_is_one_value_for_the_request(client, seeded):
    a = (await post(client, seeded["wa"], NAMES[:3])).json()
    assert a["snapshot"]["id"] and ":" in a["snapshot"]["id"]


# ---------------------------------------------------------------------------------------------- scoping

@needs_db
async def test_cross_workspace_isolation(client, seeded):
    q = "platypus sanctuary expands spring zebrafish quokka migration ERR_AUTH_403"
    a = (await post(client, seeded["wa"], NAMES, query=q, limit=50)).json()
    b = (await post(client, seeded["wb"], NAMES, query=q, limit=50)).json()
    a_docs = {x["doc_name"] for p in a["profile_results"] for x in p["results"]}
    b_docs = {x["doc_name"] for p in b["profile_results"] for x in p["results"]}
    assert "b-only.md" not in a_docs                      # positive control below: B does see it
    assert "b-only.md" in b_docs
    b_sources = set(seeded["b_ids"].values())
    assert not ({x["source_id"] for p in a["profile_results"] for x in p["results"]} & b_sources)
    # B's source ids filtered by A's token: nothing, in every profile
    r = (await post(client, seeded["wa"], NAMES, query=q, source_ids=sorted(b_sources))).json()
    assert all(p["results"] == [] for p in r["profile_results"]) and r["alignment"]["rows"] == []
    # traces are written under the caller's workspace only
    import psycopg
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        owners = {row[0] for row in c.execute("select workspace_id::text from retrieval_traces where id = any(%s::uuid[])",
                                              ([p["trace_id"] for p in a["profile_results"]],)).fetchall()}
    assert owners == {seeded["wa"]}


@needs_db
async def test_deleted_inactive_and_superseded_sources_are_excluded(client, seeded):
    gone = seeded["a_ids"]["gone.md"]
    building = seeded["a_ids"]["building.md"]
    q = "quokka migration plan secret AARDVARK_BUILDING draft ZEBRAFISH_OLD retired protocol"
    j = (await post(client, seeded["wa"], NAMES, query=q, limit=50)).json()
    ids = {x["source_id"] for p in j["profile_results"] for x in p["results"]}
    assert gone not in ids and building not in ids
    assert not any("quokka" in x["snippet"].lower() and x["source_id"] == gone for p in j["profile_results"] for x in p["results"])
    assert all("ZEBRAFISH_OLD" not in x["snippet"] for p in j["profile_results"] for x in p["results"])
    # positive control: workspace B's copy of gone.md is NOT deleted and is found
    jb = (await post(client, seeded["wb"], NAMES[:2], query="quokka migration plan secret", limit=50)).json()
    assert seeded["b_ids"]["gone.md"] in {x["source_id"] for p in jb["profile_results"] for x in p["results"]}
    # explicit source_ids pointing at the deleted source return nothing
    j2 = (await post(client, seeded["wa"], NAMES, query=q, source_ids=[gone])).json()
    assert all(p["results"] == [] for p in j2["profile_results"])


@needs_db
async def test_source_ids_filter_applies_to_every_profile(client, seeded):
    only = seeded["a_ids"]["api-errors.md"]
    j = (await post(client, seeded["wa"], NAMES, query=Q, source_ids=[only], limit=20)).json()
    for p in j["profile_results"]:
        assert p["results"] and {x["source_id"] for x in p["results"]} == {only}, p["profile"]


# ---------------------------------------------------------------------------------------------- degraded rerank

@needs_db
async def test_missing_reranker_degrades_only_the_rerank_profiles(client, seeded, monkeypatch):
    monkeypatch.setenv("SCRIBE_RERANKER", "none")
    j = (await post(client, seeded["wa"], limit=10)).json()
    by = {p["profile"]: p for p in j["profile_results"]}
    assert by["dense-rerank"]["degraded"] == ["rerank_unavailable"]
    assert by["hybrid-rrf-rerank"]["degraded"] == ["rerank_unavailable"]
    for n in ("dense-only", "lexical-only", "hybrid-rrf"):
        assert by[n]["degraded"] == []
    # degraded rerank profile = fused order, no rerank fields, nothing billed
    assert all(x["rerank_rank"] is None and x["rerank_score"] is None for x in by["dense-rerank"]["results"])
    assert by["dense-rerank"]["cost"]["rerank_documents"] == 0 and by["dense-rerank"]["cost"]["rerank_tokens"] == 0
    # ...and the degraded order equals the plain dense order of the same snapshot
    assert [x["chunk_id"] for x in by["dense-rerank"]["results"]] == [x["chunk_id"] for x in by["dense-only"]["results"]]


@needs_db
async def test_reranker_provider_error_is_degraded_with_a_code_not_a_failure(client, seeded, monkeypatch):
    failing = FakeReranker(fail_with=RerankError("TRANSIENT_PROVIDER", "provider said no", retryable=True))
    monkeypatch.setattr(compare_routes, "make_reranker_for", lambda dsn, env=None: (lambda model: failing))
    r = await post(client, seeded["wa"], ["hybrid-rrf", "hybrid-rrf-rerank"])
    assert r.status_code == 200
    by = {p["profile"]: p for p in r.json()["profile_results"]}
    assert by["hybrid-rrf-rerank"]["degraded"] == ["rerank_unavailable"] and by["hybrid-rrf-rerank"]["meta"]["rerank_error"] == "TRANSIENT_PROVIDER"
    assert by["hybrid-rrf"]["degraded"] == [] and by["hybrid-rrf-rerank"]["results"]
    # the degraded profile still yields a stored trace carrying the degraded flag
    import psycopg
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        assert c.execute("select degraded from retrieval_traces where id=%s", (by["hybrid-rrf-rerank"]["trace_id"],)).fetchone()[0] == "rerank_unavailable"


@needs_db
async def test_prices_are_not_invented_for_real_adapters(client, seeded, monkeypatch):
    class Real(FakeReranker):  # a reranker that is not stamped fake and has no configured price
        is_fake = False

    monkeypatch.setattr(compare_routes, "make_reranker_for", lambda dsn, env=None: (lambda model: Real()))
    j = (await post(client, seeded["wa"], ["dense-only", "dense-rerank"])).json()
    d = {p["profile"]: p for p in j["profile_results"]}["dense-rerank"]
    assert d["cost"]["rerank_tokens"] > 0 and d["cost"]["usd"] is None  # no price configured: unknown, not zero
    monkeypatch.setenv("SCRIBE_PRICE_RERANK_PER_MTOK_USD", "0.5")
    j = (await post(client, seeded["wa"], ["dense-only", "dense-rerank"])).json()
    d = {p["profile"]: p for p in j["profile_results"]}["dense-rerank"]
    assert d["cost"]["usd"] == pytest.approx(d["cost"]["rerank_tokens"] / 1e6 * 0.5)


# ---------------------------------------------------------------------------------------------- failures and logs

@needs_db
async def test_embedding_failure_is_503_not_a_silent_lexical_fallback(client, seeded, monkeypatch):
    from scribe_ingest.embedding import EmbeddingError

    class Boom:
        dimensions = 1536

        async def embed_queries(self, texts):
            raise EmbeddingError("INVALID_REQUEST", f"bad input: {texts[0]}", retryable=False)

    monkeypatch.setattr(compare_routes, "make_embedder_for", lambda dsn: (lambda dims: Boom()))
    r = await post(client, seeded["wa"], ["hybrid-rrf", "lexical-only"], query="CANARY-COMPARE-QUERY-TEXT")
    assert r.status_code == 503 and r.json()["error_code"] == "RETRIEVAL_FAILED"
    assert "CANARY-COMPARE-QUERY-TEXT" not in r.text


@needs_db
async def test_logs_never_contain_the_query_or_document_text(client, seeded, caplog, capfd, monkeypatch):
    caplog.set_level(logging.DEBUG)
    failing = FakeReranker(fail_with=RerankError("TRANSIENT_PROVIDER", "provider echoed CANARY-COMPARE-LOGS", retryable=True))
    monkeypatch.setattr(compare_routes, "make_reranker_for", lambda dsn, env=None: (lambda model: failing))
    q = "CANARY-COMPARE-LOGS warranty Atlas pump ignore previous instructions"
    r = await post(client, seeded["wa"], NAMES, query=q)
    assert r.status_code == 200
    out, err = capfd.readouterr()
    text = caplog.text + "\n".join(f"{x.name}|{x.getMessage()}|{x.exc_text or ''}" for x in caplog.records) + out + err
    assert "CANARY-COMPARE-LOGS" not in text and "Atlas pump" not in text
    assert any(x.name == "scribe.retrieve" or x.name == "scribe.rerank" or "rerank" in x.getMessage() for x in caplog.records)  # positive control

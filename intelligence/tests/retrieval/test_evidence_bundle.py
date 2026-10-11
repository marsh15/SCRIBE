"""T25: evidence bundles, turn-unique E-labels and the answerability gate behind POST /retrieve.

Offline: fake embedder / fake reranker only, real Postgres (DATABASE_URL_TEST). Every workspace created here is purged
by the conftest fixtures; the bundles/items are removed with them."""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from _db import aconnect, new_workspace
from _t13 import API, HR, ingest
from conftest import DATABASE_URL_TEST, SECRET, needs_db
from scribe_ingest.embedding import FakeEmbedder
from scribe_ingest.indexer import load_index_profile
from scribe_intel import evidence_bundle as eb
from scribe_intel.evidence import read_evidence_bundle, read_evidence_items
from scribe_intel.retrieval.packing import PackedContext, PackedEntry
from scribe_intel.retrieval.profiles import PROFILE_E, GateConfig, gate_for
from scribe_intel.retrieval.types import Candidate, PackedItem, RankedCandidate, SpanRef

Q = "ERR_AUTH_403 INV-2047 error code ticket"
PROFILE = "hybrid-rrf-rerank"


# ------------------------------------------------------------------ pure gate / planning (no DB)

def test_gate_placeholder_is_uncalibrated_and_rejects_nothing():
    g = PROFILE_E.gate
    assert g.tau_calibrated is False and g.tau == 0.0
    assert gate_for(PROFILE) == g and gate_for("dense-exact").tau_calibrated is False
    # the gate is NOT part of the hashed, immutable profile config (see GateConfig docstring)
    assert "tau" not in PROFILE_E.config_json and "gate" not in PROFILE_E.config_json


@pytest.mark.parametrize("entries,score,no_sources,state,reason,degraded", [
    (0, None, True, eb.INSUFFICIENT_EVIDENCE, "no_sources", False),
    (0, None, False, eb.INSUFFICIENT_EVIDENCE, "no_results", False),
    (0, 0.99, False, eb.INSUFFICIENT_EVIDENCE, "no_results", False),
    (3, 0.2, False, eb.INSUFFICIENT_EVIDENCE, "below_tau", False),
    (3, 0.5, False, eb.ANSWERABLE, "ok", False),  # score == tau passes
    (3, 0.9, False, eb.ANSWERABLE, "ok", False),
    (3, None, False, eb.ANSWERABLE, "no_rerank_score", True),  # degraded rerank / non-rerank profile: fail open, flagged
])
def test_gate_states(entries, score, no_sources, state, reason, degraded):
    a = eb.evaluate_gate(GateConfig(tau=0.5, tau_calibrated=True), entries=entries, top_rerank_score=score,
                         no_sources=no_sources)
    assert (a.state, a.reason, a.gate_degraded) == (state, reason, degraded)
    assert a.tau == 0.5 and a.tau_calibrated is True and a.top_rerank_score == score


def test_gate_reports_uncalibrated_flag():
    a = eb.evaluate_gate(GateConfig(), entries=1, top_rerank_score=0.4)
    assert a.state == eb.ANSWERABLE and a.as_json()["tau_calibrated"] is False


def test_retrieval_failed_is_distinct_from_insufficient():
    f = eb.retrieval_failed(GateConfig())
    assert f.state == eb.RETRIEVAL_FAILED != eb.INSUFFICIENT_EVIDENCE and f.reason == "retrieval_failed"


def _cand(i: int, content: str, pages: list[int | None]) -> RankedCandidate:
    spans = [SpanRef(uuid.uuid4(), n, 0, len(content), p, None) for n, p in enumerate(pages)]
    c = Candidate(chunk_id=uuid.uuid4(), parent_id=uuid.uuid4(), source_id=i, source_version_id=uuid.uuid4(),
                  generation_id=uuid.uuid4(), doc_name=f"d{i}.md", content=content, heading_text="H", content_hash="h",
                  token_count=3, method="dense", rank=i, score=0.1, spans=spans)
    return RankedCandidate(chunk_id=c.chunk_id, candidate=c)


def test_plan_items_uses_child_text_labels_and_never_estimates_pages():
    a, b, c = _cand(1, "child A", [3, 5]), _cand(2, "child B", [None]), _cand(3, "child C", [])
    packed = PackedContext(entries=[
        PackedEntry(PackedItem(x.chunk_id, x.candidate.parent_id, f"PARENT {x.candidate.content}", "H", "d", 1,
                               x.candidate.source_version_id, x.candidate.spans, 5, None, True))
        for x in (a, b, c)], used_tokens=15, budget_tokens=100)
    drafts = eb.plan_items(packed, [a, b, c], 7)
    assert [d.label for d in drafts] == ["E7", "E8", "E9"]
    assert [d.quote for d in drafts] == ["child A", "child B", "child C"]  # the cited child span, not the parent
    assert drafts[0].quote_hash == hashlib.sha256(b"child A").hexdigest()
    assert [(d.page_start, d.page_end) for d in drafts] == [(3, 5), (None, None), (None, None)]
    assert eb.plan_items(None, [a], 1) == []


# ------------------------------------------------------------------ endpoint + DB

def token(wid: str, scope: str = "retrieve") -> str:
    now = int(time.time())
    return jwt.encode({"sub": "user_1", "wid": wid, "scope": scope, "aud": "scribe-intel", "iat": now, "exp": now + 60},
                      SECRET, algorithm="HS256")


def auth(wid, scope="retrieve"):
    return {"authorization": f"Bearer {token(wid, scope)}"}


async def _seed() -> dict:
    conn = await aconnect(DATABASE_URL_TEST)
    try:
        emb, profile = FakeEmbedder(), await load_index_profile(conn)
        wa, wb, empty = await new_workspace(conn), await new_workspace(conn), await new_workspace(conn)
        await ingest(conn, wa, "api-errors.md", API, emb, profile)
        await ingest(conn, wa, "hr.md", HR, emb, profile)
        await ingest(conn, wb, "hr.md", HR, emb, profile)
        return {"wa": wa, "wb": wb, "empty": empty}
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def seeded():
    if not DATABASE_URL_TEST:
        pytest.skip("DATABASE_URL_TEST not set")
    return asyncio.run(_seed())


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


@pytest.fixture()
async def conn():
    c = await aconnect(DATABASE_URL_TEST)
    yield c
    await c.close()


async def post(client, wid, profile=PROFILE, query=Q, **kw):
    return await client.post("/api/py/retrieve", json={"query": query, "profile": profile, **kw}, headers=auth(wid))


@needs_db
async def test_labels_are_unique_across_two_searches_in_one_turn(client, conn, seeded):
    wid = seeded["wa"]
    first = (await post(client, wid)).json()
    ev1 = first["evidence"]
    assert ev1["bundle_id"] and ev1["items"]
    n1 = len(ev1["items"])
    assert [i["label"] for i in ev1["items"]] == [f"E{k}" for k in range(1, n1 + 1)]
    assert ev1["next_label"] == n1 + 1

    second = (await post(client, wid, query="annual leave days per year", next_label=ev1["next_label"])).json()
    ev2 = second["evidence"]
    labels2 = [i["label"] for i in ev2["items"]]
    assert labels2 == [f"E{k}" for k in range(n1 + 1, n1 + 1 + len(labels2))]
    assert not {i["label"] for i in ev1["items"]} & set(labels2)  # unique across the turn
    assert ev1["bundle_id"] != ev2["bundle_id"] and ev2["next_label"] == n1 + 1 + len(labels2)
    # the context handed to the model carries the same labels and item ids
    ctx = second["context"]["entries"]
    assert [e["label"] for e in ctx] == labels2
    assert [e["evidence_item_id"] for e in ctx] == [i["id"] for i in ev2["items"]]
    # per bundle the DB agrees, and the same label may never repeat inside a bundle
    for ev in (ev1, ev2):
        cur = await conn.execute("select label from evidence_items where bundle_id=%s", (ev["bundle_id"],))
        assert sorted(r[0] for r in await cur.fetchall()) == sorted(i["label"] for i in ev["items"])


@needs_db
async def test_default_label_start_and_validation(client, seeded):
    j = (await post(client, seeded["wa"])).json()
    assert j["evidence"]["items"][0]["label"] == "E1"
    for bad in (0, -1, 100_001):
        assert (await post(client, seeded["wa"], next_label=bad)).status_code == 422


@needs_db
async def test_quote_is_the_child_span_and_hash_matches(client, conn, seeded):
    wid = seeded["wa"]
    j = (await post(client, wid)).json()
    items = j["evidence"]["items"]
    assert items
    for it in items:
        cur = await conn.execute(
            "select e.quote, e.quote_hash, c.content, e.source_version_id, c.source_version_id, e.workspace_id "
            "from evidence_items e join chunks c on c.id = e.chunk_id where e.id=%s", (it["id"],))
        quote, qhash, content, ev_ver, ch_ver, ev_ws = await cur.fetchone()
        assert quote == content  # the child chunk's own text, never the expanded parent
        assert qhash == hashlib.sha256(quote.encode()).hexdigest()
        assert str(ev_ver) == it["source_version_id"] == str(ch_ver) and str(ev_ws) == wid
        assert it["source_name"] in ("api-errors.md", "hr.md")
    # read back through the soft-delete-aware reader (INV-8): same quote and hash
    views = await read_evidence_bundle(conn, wid, uuid.UUID(j["evidence"]["bundle_id"]))
    assert {v.label for v in views} == {i["label"] for i in items}
    assert all(v.readable and v.quote and v.quote_hash == hashlib.sha256(v.quote.encode()).hexdigest() for v in views)


@needs_db
async def test_null_page_stays_null_and_known_pages_are_reported(client, conn, seeded):
    wid = seeded["wa"]
    items = (await post(client, wid)).json()["evidence"]["items"]
    # Markdown sources have no pages: nothing may be estimated
    assert all(i["page_start"] is None and i["page_end"] is None for i in items)
    # a PDF-like source whose spans carry pages: min/max of the child's span pages
    pw = await new_workspace(conn)
    emb, profile = FakeEmbedder(), await load_index_profile(conn)
    src = await ingest(conn, pw, "paged.md", API, emb, profile)
    await conn.execute("update chunk_source_spans set page=4 where workspace_id=%s", (pw,))
    j = (await post(client, pw)).json()
    its = j["evidence"]["items"]
    assert its and all(i["page_start"] == 4 and i["page_end"] == 4 for i in its)
    assert all(i["source_name"] == "paged.md" and i["heading_path"] for i in its)  # heading path from the block
    assert src["id"]


@needs_db
async def test_persisted_rows_bundle_trace_and_answerability(client, conn, seeded):
    wid = seeded["wa"]
    j = (await post(client, wid)).json()
    cur = await conn.execute("select trace_id, workspace_id, answerability, top_rerank_score from evidence_bundles where id=%s",
                             (j["evidence"]["bundle_id"],))
    trace_id, ws, state, top = await cur.fetchone()
    assert str(trace_id) == j["trace_id"] and str(ws) == wid
    assert state == eb.ANSWERABLE == j["answerability"]["state"]
    assert top == j["meta"]["top_rerank_score"] == j["answerability"]["top_rerank_score"]
    cur = await conn.execute("select count(*), count(distinct label) from evidence_items where bundle_id=%s",
                             (j["evidence"]["bundle_id"],))
    n, distinct = await cur.fetchone()
    assert n == distinct == len(j["context"]["entries"]) == len(j["evidence"]["items"])


@needs_db
async def test_default_gate_is_uncalibrated_and_reported(client, seeded):
    a = (await post(client, seeded["wa"])).json()["answerability"]
    assert a["state"] == eb.ANSWERABLE and a["reason"] == "ok"
    assert a["tau_calibrated"] is False and a["tau"] == 0.0 and a["gate_degraded"] is False


@needs_db
async def test_score_below_tau_is_insufficient(client, conn, seeded, monkeypatch):
    from scribe_intel import retrieve_routes

    monkeypatch.setattr(retrieve_routes, "gate_for", lambda name: GateConfig(tau=1.5, tau_calibrated=True))
    j = (await post(client, seeded["wa"])).json()
    a = j["answerability"]
    assert (a["state"], a["reason"], a["tau"], a["tau_calibrated"]) == (eb.INSUFFICIENT_EVIDENCE, "below_tau", 1.5, True)
    cur = await conn.execute("select answerability from evidence_bundles where id=%s", (j["evidence"]["bundle_id"],))
    assert (await cur.fetchone())[0] == eb.INSUFFICIENT_EVIDENCE


@needs_db
@pytest.mark.parametrize("mode", ["rerank_none", "non_rerank_profile"])
async def test_no_rerank_score_fails_open_and_is_flagged(client, seeded, monkeypatch, mode):
    if mode == "rerank_none":
        monkeypatch.setenv("SCRIBE_RERANKER", "none")
        j = (await post(client, seeded["wa"])).json()
        assert "rerank_unavailable" in j["degraded"]
    else:
        j = (await post(client, seeded["wa"], profile="hybrid-rrf")).json()
    a = j["answerability"]
    assert (a["state"], a["reason"], a["gate_degraded"], a["top_rerank_score"]) == (eb.ANSWERABLE, "no_rerank_score", True, None)
    assert j["evidence"]["items"]


@needs_db
async def test_empty_workspace_is_insufficient_no_sources(client, conn, seeded):
    j = (await post(client, seeded["empty"])).json()
    assert j["answerability"]["state"] == eb.INSUFFICIENT_EVIDENCE and j["answerability"]["reason"] == "no_sources"
    assert j["evidence"]["items"] == [] and j["evidence"]["bundle_id"] and j["evidence"]["next_label"] == 1
    assert j["context"]["entries"] == []
    cur = await conn.execute("select answerability from evidence_bundles where id=%s", (j["evidence"]["bundle_id"],))
    assert (await cur.fetchone())[0] == eb.INSUFFICIENT_EVIDENCE


@needs_db
async def test_no_matching_results_with_sources_is_no_results(client, seeded):
    j = (await post(client, seeded["wa"], profile="lexical-only", query="zzqqxxvv wwkkjjyy")).json()
    assert j["answerability"]["state"] == eb.INSUFFICIENT_EVIDENCE and j["answerability"]["reason"] == "no_results"
    assert j["evidence"]["items"] == []


@needs_db
async def test_retrieval_failure_is_distinct_from_insufficient(client, seeded, monkeypatch):
    from scribe_intel import retrieve_routes

    async def boom(ctx):
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(retrieve_routes, "get_handler", lambda name: boom)
    r = await post(client, seeded["wa"])
    j = r.json()
    assert r.status_code == 503 and j["error_code"] == "RETRIEVAL_FAILED"
    assert j["answerability"]["state"] == eb.RETRIEVAL_FAILED != eb.INSUFFICIENT_EVIDENCE


@needs_db
async def test_cross_workspace_bundle_and_item_reads_404(client, conn, seeded):
    wa, wb = seeded["wa"], seeded["wb"]
    ev = (await post(client, wa)).json()["evidence"]
    item = ev["items"][0]["id"]
    # reader: another workspace sees nothing
    assert await read_evidence_bundle(conn, wb, uuid.UUID(ev["bundle_id"])) == []
    assert await read_evidence_items(conn, wb, [uuid.UUID(item)]) == []
    assert len(await read_evidence_bundle(conn, wa, uuid.UUID(ev["bundle_id"]))) == len(ev["items"])
    # HTTP: a token for workspace B cannot open workspace A's item; workspace A can
    r = await client.post("/api/py/evidence/open", json={"item_id": item}, headers=auth(wb, "evidence_read"))
    assert r.status_code == 404
    r = await client.post("/api/py/evidence/open", json={"item_id": item}, headers=auth(wa, "evidence_read"))
    assert r.status_code == 200 and r.json()["label"] == ev["items"][0]["label"]


@needs_db
async def test_workspace_in_body_is_ignored_for_bundles(client, conn, seeded):
    r = await client.post("/api/py/retrieve", json={"query": Q, "profile": PROFILE, "workspace_id": seeded["wb"]},
                          headers=auth(seeded["wa"]))
    ev = r.json()["evidence"]
    cur = await conn.execute("select distinct workspace_id from evidence_items where bundle_id=%s", (ev["bundle_id"],))
    assert [str(x[0]) for x in await cur.fetchall()] == [seeded["wa"]]


@needs_db
async def test_unpacked_strategy_has_no_bundle(client, seeded):
    j = (await post(client, seeded["wa"], profile="dense-exact")).json()
    assert j["evidence"] == {"bundle_id": None, "next_label": 1, "items": []}
    assert j["answerability"]["state"] == eb.ANSWERABLE and j["answerability"]["gate_degraded"] is True


@needs_db
async def test_bundle_write_failure_offers_no_labels(client, seeded, monkeypatch):
    from scribe_intel import retrieve_routes

    async def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(retrieve_routes, "persist_trace", boom)
    j = (await post(client, seeded["wa"])).json()
    assert "evidence_not_recorded" in j["degraded"] and "trace_persist_failed" in j["degraded"]
    assert j["evidence"] == {"bundle_id": None, "next_label": 1, "items": []}
    assert all(e["label"] is None and e["evidence_item_id"] is None for e in j["context"]["entries"])

"""Exact retrieval diagnostics: workspace scope, bounded rows and source redaction."""
from __future__ import annotations

import time
import uuid

import httpx
import jwt
import pytest
from _db import aconnect, new_workspace, skip_or_fail_if_foreign_jobs
from conftest import DATABASE_URL_TEST, SECRET, needs_db
from psycopg_pool import AsyncConnectionPool
from test_source_delete_purge import build_live_source, soft_delete

pytestmark = needs_db


def auth(wid, scope="retrieval_trace_read"):
    now = int(time.time())
    token = jwt.encode({"sub": "user_1", "wid": wid, "scope": scope, "aud": "scribe-intel", "iat": now, "exp": now + 60}, SECRET, algorithm="HS256")
    return {"authorization": f"Bearer {token}"}


@pytest.fixture
async def env(monkeypatch):
    from scribe_intel import trace_routes
    from scribe_intel.app import create_app

    conn = await aconnect(DATABASE_URL_TEST)
    await skip_or_fail_if_foreign_jobs(conn)
    wid, other = await new_workspace(conn), await new_workspace(conn)

    async def configure(c):
        await c.execute("set session authorization scribe_app")

    pool = AsyncConnectionPool(DATABASE_URL_TEST, min_size=0, max_size=2, open=False,
                               kwargs={"autocommit": True, "prepare_threshold": None}, configure=configure)
    await pool.open()
    async def get_test_pool():
        return pool
    monkeypatch.setattr(trace_routes, "get_pool", get_test_pool)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        yield conn, wid, other, client
    await pool.close()
    await conn.execute("delete from usage_events where source_id in (select id::text from files where workspace_id=%s)", (wid,))
    await conn.execute("delete from ingestion_jobs where file_id in (select id from files where workspace_id=%s)", (wid,))
    await conn.close()


async def seed(conn, wid):
    fid, _ = await build_live_source(conn, wid)
    chunk, version = await (await conn.execute("select c.id,c.source_version_id from chunks c join source_versions v on v.id=c.source_version_id where v.source_id=%s limit 1", (fid,))).fetchone()
    trace = (await (await conn.execute("insert into retrieval_traces(workspace_id,query,status,degraded,counts,stage_ms,cost) values (%s,'question','ok','rerank',%s,%s,%s) returning id", (wid, '{"profile":"hybrid"}', '{"dense":10}', '{"tokens":2}'))).fetchone())[0]
    await conn.execute("insert into evidence_bundles(trace_id,workspace_id,answerability,top_rerank_score) values (%s,%s,'ANSWERABLE',0.75)", (trace,wid))
    await conn.execute("insert into retrieval_candidates(trace_id,workspace_id,chunk_id,dense_rank,dense_score,lexical_rank,fusion_rank,rerank_rank,rerank_score,selected) values (%s,%s,%s,3,0.6,2,1,1,0.75,true)", (trace,wid,chunk))
    return fid, str(trace), str(chunk), str(version)


async def test_trace_authorization_details_and_redaction(env):
    conn, wid, other, client = env
    fid, trace, chunk, version = await seed(conn, wid)
    body = {"trace_id": trace}
    assert (await client.post("/retrieval/trace", json=body)).status_code == 401
    assert (await client.post("/retrieval/trace", json=body, headers=auth(wid, "evidence_read"))).status_code == 403
    foreign = await client.post("/retrieval/trace", json={**body, "workspace_id": wid}, headers=auth(other))
    missing = await client.post("/retrieval/trace", json={"trace_id": str(uuid.uuid4())}, headers=auth(wid))
    assert foreign.status_code == missing.status_code == 404 and foreign.json() == missing.json()
    for path in ("/retrieval/trace", "/api/py/retrieval/trace"):
        r = await client.post(path, json=body, headers=auth(wid))
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["trace"]["profile"] == "hybrid" and j["trace"]["degraded"] == ["rerank"]
        assert j["trace"]["stage_ms"] == {"dense": 10} and j["trace"]["cost"] == {"tokens": 2}
        assert j["answerability"] == {"state": "ANSWERABLE", "top_rerank_score": 0.75}
        c = j["candidates"][0]
        assert c["chunk_id"] == chunk and c["source_id"] == fid and c["source_version_id"] == version
        assert c["version_number"] == 1 and c["dense_rank"] == 3 and c["selected"] is True
        assert "content" not in r.text and "quote" not in r.text and not c["source_unavailable"]
    assert await soft_delete(conn, wid, fid)
    r = await client.post("/retrieval/trace", json=body, headers=auth(wid))
    c = r.json()["candidates"][0]
    assert c["source_unavailable"] and all(c[k] is None for k in ("chunk_id", "source_id", "source_name", "source_version_id", "version_number"))
    await conn.execute("delete from chunks where id=%s", (chunk,))
    c = (await client.post("/retrieval/trace", json=body, headers=auth(wid))).json()["candidates"][0]
    assert c["source_unavailable"] and c["chunk_id"] is None


async def test_candidates_are_bounded_and_ordered(env):
    conn, wid, _, client = env
    trace = (await (await conn.execute("insert into retrieval_traces(workspace_id,query,status) values (%s,'q','ok') returning id", (wid,))).fetchone())[0]
    await conn.execute("insert into retrieval_candidates(trace_id,workspace_id,fusion_rank,selected) select %s,%s,n,false from generate_series(101,1,-1) n", (trace,wid))
    j = (await client.post("/retrieval/trace", json={"trace_id": str(trace)}, headers=auth(wid))).json()
    assert j["answerability"] is None and j["candidates_truncated"] is True
    assert [c["fusion_rank"] for c in j["candidates"]] == list(range(1,101))
    assert all(c["source_unavailable"] for c in j["candidates"])


async def test_invalid_id_and_database_failure_are_safe(env, monkeypatch):
    from scribe_intel import trace_routes
    _, wid, _, client = env
    assert (await client.post("/retrieval/trace", json={"trace_id": "invalid"}, headers=auth(wid))).status_code == 422

    async def unavailable():
        raise RuntimeError("private connection details")

    monkeypatch.setattr(trace_routes, "get_pool", unavailable)
    r = await client.post("/retrieval/trace", json={"trace_id": str(uuid.uuid4())}, headers=auth(wid))
    assert r.status_code == 503 and r.json()["error_code"] == "TRACE_UNAVAILABLE"
    assert "private connection details" not in r.text

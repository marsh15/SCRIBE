"""T12: POST /api/py/ingest/source (auth, workspace from token only, error mapping)."""
from __future__ import annotations

import time

import httpx
import jwt
import pytest
from _db import aconnect, doc, new_file, new_workspace
from conftest import DATABASE_URL_TEST, SECRET, needs_db

pytestmark = needs_db


def token(wid: str, scope: str = "ingest") -> str:
    now = int(time.time())
    return jwt.encode({"sub": "user_1", "wid": wid, "scope": scope, "aud": "scribe-intel", "iat": now, "exp": now + 60},
                      SECRET, algorithm="HS256")


@pytest.fixture()
async def client(monkeypatch):
    from scribe_intel import db, ingest_routes
    from scribe_intel.app import create_app
    from scribe_intel.config import get_settings

    monkeypatch.setenv("DATABASE_URL", DATABASE_URL_TEST)
    monkeypatch.setenv("SCRIBE_EMBEDDER", "fake")
    monkeypatch.setenv("SCRIBE_ALLOW_FAKE_EMBEDDINGS", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(ingest_routes, "download_private_blob", lambda url, tok, **k: doc(1))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c
    await db.close_pool()
    get_settings.cache_clear()


@pytest.fixture()
async def ws():
    conn = await aconnect(DATABASE_URL_TEST)
    a, b = await new_workspace(conn), await new_workspace(conn)
    fid = await new_file(conn, a, storage_url="https://blob.example/x.md")
    yield conn, a, b, fid
    await conn.close()


def auth(wid, scope="ingest"):
    return {"authorization": f"Bearer {token(wid, scope)}"}


async def test_success_shape_and_state(client, ws):
    conn, a, _, fid = ws
    r = await client.post("/api/py/ingest/source", json={"source_id": fid}, headers=auth(a))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "ACTIVE" and j["generation_id"] and j["chunks"] >= 2
    assert j["embedded"] == j["expected"] > 0 and "total" in j["timings_ms"] and j["warnings"] == []
    cur = await conn.execute("select status, active_version_id from files where id=%s", (fid,))
    assert (await cur.fetchone())[0] == "ready"
    again = await client.post("/ingest/source", json={"source_id": fid}, headers=auth(a))
    assert again.status_code == 200 and again.json()["generation_id"] == j["generation_id"]


async def test_rejects_token_without_ingest_scope(client, ws):
    _, a, _, fid = ws
    r = await client.post("/api/py/ingest/source", json={"source_id": fid}, headers=auth(a, "health retrieve"))
    assert r.status_code == 403
    r = await client.post("/api/py/ingest/source", json={"source_id": fid})
    assert r.status_code == 401


async def test_workspace_comes_from_token_not_body(client, ws):
    _, a, b, fid = ws
    # token for B, body claims A: must not reach A's file
    r = await client.post("/api/py/ingest/source", json={"source_id": fid, "workspace_id": a}, headers=auth(b))
    assert r.status_code == 404
    r = await client.post("/api/py/ingest/source", json={"source_id": fid, "workspace_id": b}, headers=auth(a))
    assert r.status_code == 200


async def test_missing_storage_url_is_422(client, ws):
    conn, a, _, _ = ws
    fid = await new_file(conn, a)
    r = await client.post("/api/py/ingest/source", json={"source_id": fid}, headers=auth(a))
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "NO_STORAGE_URL"


async def test_permanent_vs_retryable_status_mapping(client, ws, monkeypatch):
    from scribe_intel import ingest_routes
    from scribe_ingest.blob import BlobDownloadError
    from scribe_ingest.embedding import FakeEmbedder

    _, a, _, fid = ws

    def boom(url, tok, **k):
        raise BlobDownloadError("down", status=503, retryable=True)
    monkeypatch.setattr(ingest_routes, "download_private_blob", boom)
    r = await client.post("/api/py/ingest/source", json={"source_id": fid}, headers=auth(a))
    assert r.status_code == 503 and r.json()["detail"]["retryable"] is True

    monkeypatch.setattr(ingest_routes, "download_private_blob", lambda *a, **k: b"\x00\x01 not a doc")
    monkeypatch.setattr(ingest_routes, "embedder_from_env", lambda dsn: FakeEmbedder())
    conn = ws[0]
    await conn.execute("update files set type='image/png' where id=%s", (fid,))
    r = await client.post("/api/py/ingest/source", json={"source_id": fid}, headers=auth(a))
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "UNSUPPORTED_FORMAT"


async def test_fake_embedder_refused_without_opt_in(client, ws, monkeypatch):
    _, a, _, fid = ws
    monkeypatch.delenv("SCRIBE_ALLOW_FAKE_EMBEDDINGS")
    r = await client.post("/api/py/ingest/source", json={"source_id": fid}, headers=auth(a))
    assert r.status_code == 500 and r.json()["detail"]["error_code"] == "FAKE_EMBEDDER_REFUSED"

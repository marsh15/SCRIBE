"""T23: POST /evidence/open (scope, workspace from token, 404 across workspaces, source-deleted, OCR no-bbox, page)."""
from __future__ import annotations

import time

import httpx
import jwt
import pytest
from _db import aconnect, new_file, new_workspace
from conftest import DATABASE_URL_TEST, SECRET, needs_db
from test_source_delete_purge import build_live_source, soft_delete

pytestmark = needs_db
BBOX = [10.0, 20.0, 110.0, 40.0]


def auth(wid, scope="evidence_read"):
    now = int(time.time())
    tok = jwt.encode({"sub": "user_1", "wid": wid, "scope": scope, "aud": "scribe-intel", "iat": now, "exp": now + 60},
                     SECRET, algorithm="HS256")
    return {"authorization": f"Bearer {tok}"}


@pytest.fixture()
async def client(monkeypatch):
    from scribe_intel import db
    from scribe_intel.app import create_app
    from scribe_intel.config import get_settings

    monkeypatch.setenv("DATABASE_URL", DATABASE_URL_TEST)
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c
    await db.close_pool()
    get_settings.cache_clear()


@pytest.fixture()
async def env(monkeypatch):
    from scribe_intel import ingest_steps
    conn = await aconnect(DATABASE_URL_TEST)
    cur = await conn.execute("select count(*) from ingestion_jobs where status in ('queued','retrying','processing')")
    if (await cur.fetchone())[0]:
        await conn.close()
        pytest.skip("foreign claimable jobs present in the shared DB")
    wid = await new_workspace(conn)
    real = ingest_steps.embed_generation
    monkeypatch.setattr(ingest_steps, "embed_generation", lambda *a, **k: real(*a, **{**k, "batch_size": 2}))
    yield conn, wid
    await conn.execute("delete from usage_events where source_id in (select id::text from files where workspace_id=%s)", (wid,))
    await conn.execute("delete from ingestion_jobs where file_id in (select id from files where workspace_id=%s)", (wid,))
    await conn.close()


async def seed(conn, wid):
    from test_source_delete_purge import seed_versions_and_evidence
    fid, _ = await build_live_source(conn, wid)
    _, _, item = await seed_versions_and_evidence(conn, wid, fid)
    return fid, str(item)


async def set_geometry(conn, item, *, ocr, page=3, bbox=BBOX):
    cur = await conn.execute("select chunk_id from evidence_items where id=%s", (item,))
    chunk = (await cur.fetchone())[0]
    await conn.execute("update document_blocks set ocr=%s where id in (select block_id from chunk_source_spans where chunk_id=%s)",
                       (ocr, chunk))
    await conn.execute("update chunk_source_spans set page=%s, bbox=%s where chunk_id=%s", (page, bbox, chunk))


async def test_requires_the_evidence_read_scope(client, env):
    conn, wid = env
    _, item = await seed(conn, wid)
    assert (await client.post("/evidence/open", json={"item_id": item})).status_code == 401
    r = await client.post("/evidence/open", json={"item_id": item}, headers=auth(wid, "retrieve"))
    assert r.status_code == 403


async def test_opens_with_version_page_bbox_and_quote(client, env):
    conn, wid = env
    fid, item = await seed(conn, wid)
    await set_geometry(conn, item, ocr=False)
    for path in ("/evidence/open", "/api/py/evidence/open"):
        r = await client.post(path, json={"item_id": item}, headers=auth(wid))
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["source_deleted"] is False and j["quote"] == "The quick brown fox jumps."
        assert j["source_id"] == fid and j["version_number"] == 1 and j["source_version_id"]
        assert j["page"] == 3 and j["heading_path"] == ["Title 1"] and j["ocr"] is False and j["ocr_notice"] is None
        assert j["spans"] and all(s["bbox"] == BBOX and s["page"] == 3 for s in j["spans"])


async def test_ocr_spans_never_carry_a_bbox_and_show_the_notice(client, env):
    conn, wid = env
    _, item = await seed(conn, wid)
    await set_geometry(conn, item, ocr=True)  # bbox stored despite OCR: the endpoint must still drop it
    j = (await client.post("/evidence/open", json={"item_id": item}, headers=auth(wid))).json()
    assert j["ocr"] is True and j["ocr_notice"] == "OCR text — verify against original"
    assert j["spans"] and all(s["bbox"] is None and s["ocr"] for s in j["spans"])
    assert j["page"] == 3


async def test_unknown_page_stays_null(client, env):
    conn, wid = env
    _, item = await seed(conn, wid)
    await conn.execute("update evidence_items set page_start=null where id=%s", (item,))
    await set_geometry(conn, item, ocr=False, page=None, bbox=None)
    j = (await client.post("/evidence/open", json={"item_id": item}, headers=auth(wid))).json()
    assert j["page"] is None  # INV-7: never estimated


async def test_another_workspace_gets_404_and_workspace_in_body_is_ignored(client, env):
    conn, wid = env
    other = await new_workspace(conn)
    _, item = await seed(conn, wid)
    r = await client.post("/evidence/open", json={"item_id": item, "workspace_id": wid}, headers=auth(other))
    assert r.status_code == 404 and "quote" not in r.text
    r = await client.post("/evidence/open", json={"item_id": "00000000-0000-4000-8000-000000000000"}, headers=auth(wid))
    assert r.status_code == 404
    assert (await client.post("/evidence/open", json={"item_id": "nope"}, headers=auth(wid))).status_code == 422


async def test_deleted_source_withholds_everything(client, env):
    conn, wid = env
    fid, item = await seed(conn, wid)
    assert await soft_delete(conn, wid, fid)
    r = await client.post("/evidence/open", json={"item_id": item}, headers=auth(wid))
    assert r.status_code == 200
    j = r.json()
    assert j == {"item_id": item, "label": "E1", "source_deleted": True, "notice": "Source deleted"}

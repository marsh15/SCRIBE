"""T27: ids from another workspace on every Python endpoint that takes one (evidence item, bundle, message, source) answer
exactly like an unknown id (INV-8, INV-15); a workspace in the request body is never read; and the SECURITY DEFINER SQL
functions the Next routes call refuse a foreign (workspace, user, source) triple. Shared test DB, fake providers."""
from __future__ import annotations

import uuid

import httpx
import psycopg
import pytest
from _adv import HYBRID, auth, seed_sync
from conftest import DATABASE_URL_TEST, needs_db
from test_verify import FakeVerifier, use_verifier

pytestmark = needs_db
WORKSPACE_KEYS = {"workspace_id": None, "wid": None, "workspaceId": None, "workspace": None, "tenant_id": None}


@pytest.fixture(scope="module")
def seeded():
    return seed_sync(DATABASE_URL_TEST)


@pytest.fixture()
async def client(monkeypatch):
    from scribe_intel import db
    from scribe_intel.app import create_app
    from scribe_intel.config import get_settings

    for k, v in dict(DATABASE_URL=DATABASE_URL_TEST, SCRIBE_EMBEDDER="fake", SCRIBE_ALLOW_FAKE_EMBEDDINGS="1",
                     SCRIBE_RERANKER="fake", SCRIBE_ALLOW_FAKE_RERANKER="1").items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c
    await db.close_pool()
    get_settings.cache_clear()


def with_ws(body: dict, wid: str) -> dict:
    """The same body with every spelling of 'use this workspace' pointing at `wid`."""
    return {**body, **{k: wid for k in WORKSPACE_KEYS}}


async def tenant_b_evidence(client, seeded, monkeypatch):
    """Workspace B retrieves its own secret document: a real bundle, item, message and stored verification."""
    wb = seeded["wb"]
    fid = next(iter(seeded["b_ids"].values()))
    r = await client.post("/retrieve", json={"query": "Bravo launch codename", "profile": HYBRID, "source_ids": [fid]},
                          headers=auth(wb, "retrieve"))
    assert r.status_code == 200, r.text
    ev = r.json()["evidence"]
    assert ev["items"], "workspace B must have evidence to be attacked"
    bundle = {"bundle_id": ev["bundle_id"], "items": [
        {"label": i["label"], "item_id": i["id"], "source_version_id": i["source_version_id"]} for i in ev["items"]]}
    use_verifier(monkeypatch, FakeVerifier())
    msg = seeded["msgs"]["b"]["message"]
    v = await client.post("/verify", json={"message_id": msg, "bundles": [bundle], "claims": [
        {"index": 0, "text": f"The codename is secret [{ev['items'][0]['label']}]."}]}, headers=auth(wb, "verify"))
    assert v.status_code == 200, v.text
    return {"item_id": ev["items"][0]["id"], "bundle": bundle, "message": msg, "source_id": fid}


async def test_evidence_open_foreign_item_is_indistinguishable_from_unknown(client, seeded, monkeypatch):
    b = await tenant_b_evidence(client, seeded, monkeypatch)
    wa = seeded["wa"]
    own = await client.post("/evidence/open", json={"item_id": b["item_id"]}, headers=auth(seeded["wb"], "evidence_read"))
    assert own.status_code == 200 and "BRAVO-ONLY-SECRET-4821" in own.json()["quote"]
    foreign = await client.post("/evidence/open", json=with_ws({"item_id": b["item_id"]}, seeded["wb"]),
                                headers=auth(wa, "evidence_read"))
    unknown = await client.post("/evidence/open", json={"item_id": str(uuid.uuid4())}, headers=auth(wa, "evidence_read"))
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()
    assert "BRAVO" not in foreign.text


async def test_verify_foreign_bundle_and_message_are_indistinguishable_from_unknown(client, seeded, monkeypatch):
    b = await tenant_b_evidence(client, seeded, monkeypatch)
    wa = seeded["wa"]
    claim = [{"index": 0, "text": "The codename is secret [E1]."}]

    async def verify(bundle, message):
        body = with_ws({"bundles": [bundle] if bundle else [], "claims": claim, **({"message_id": message} if message else {})},
                       seeded["wb"])
        return await client.post("/verify", json=body, headers=auth(wa, "verify"))

    foreign_bundle = await verify(b["bundle"], None)
    unknown_bundle = await verify({**b["bundle"], "bundle_id": str(uuid.uuid4())}, None)
    assert foreign_bundle.status_code == unknown_bundle.status_code == 404
    assert foreign_bundle.json() == unknown_bundle.json()
    foreign_msg = await verify(None, b["message"])
    unknown_msg = await verify(None, f"m-{uuid.uuid4()}")
    assert foreign_msg.status_code == unknown_msg.status_code == 404
    assert foreign_msg.json() == unknown_msg.json()
    assert "BRAVO" not in foreign_bundle.text + foreign_msg.text


async def test_verify_read_never_returns_another_workspaces_rows(client, seeded, monkeypatch):
    b = await tenant_b_evidence(client, seeded, monkeypatch)
    own = await client.post("/verify/read", json={"message_ids": [b["message"]]}, headers=auth(seeded["wb"], "verify"))
    assert own.status_code == 200 and own.json()["messages"][b["message"]]
    foreign = await client.post("/verify/read", json=with_ws({"message_ids": [b["message"]]}, seeded["wb"]),
                                headers=auth(seeded["wa"], "verify"))
    unknown = await client.post("/verify/read", json={"message_ids": [f"m-{uuid.uuid4()}"]}, headers=auth(seeded["wa"], "verify"))
    assert foreign.status_code == unknown.status_code == 200
    assert foreign.json() == unknown.json() == {"messages": {}}


async def test_ingest_source_foreign_source_id_is_404_like_an_unknown_one_and_the_body_workspace_is_ignored(client, seeded):
    wa, wb = seeded["wa"], seeded["wb"]
    fid_b = next(iter(seeded["b_ids"].values()))
    for body in ({"source_id": fid_b}, with_ws({"source_id": fid_b}, wb)):
        r = await client.post("/ingest/source", json=body, headers=auth(wa, "ingest"))
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["error_code"] == "SOURCE_NOT_FOUND"
    unknown = await client.post("/ingest/source", json={"source_id": 2_000_000_000}, headers=auth(wa, "ingest"))
    assert unknown.status_code == 404 and unknown.json() == r.json()
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:  # B's source was not touched
        assert c.execute("select count(*) from source_versions where source_id=%s", (fid_b,)).fetchone()[0] == 1


async def test_retrieve_source_ids_of_another_workspace_return_nothing(client, seeded):
    fid_b = next(iter(seeded["b_ids"].values()))
    for profile in (HYBRID, "lexical-v1", "dense-exact"):
        r = await client.post("/retrieve", json=with_ws({"query": "Bravo launch codename BRAVO-ONLY-SECRET-4821",
                                                         "profile": profile, "source_ids": [fid_b]}, seeded["wb"]),
                              headers=auth(seeded["wa"], "retrieve"))
        assert r.status_code == 200 and r.json()["candidates"] == [], profile


async def test_compare_source_ids_of_another_workspace_return_nothing_in_every_profile(client, seeded):
    """T29: /compare takes source_ids too; a foreign id is indistinguishable from an unknown one (empty rankings)."""
    fid_b = next(iter(seeded["b_ids"].values()))
    names = ["dense-only", "lexical-only", "hybrid-rrf", "hybrid-rrf-rerank"]
    foreign = await client.post("/compare", json=with_ws({"query": "Bravo launch codename BRAVO-ONLY-SECRET-4821",
                                                          "profiles": names, "source_ids": [fid_b]}, seeded["wb"]),
                                headers=auth(seeded["wa"], "compare"))
    unknown = await client.post("/compare", json={"query": "Bravo launch codename BRAVO-ONLY-SECRET-4821",
                                                  "profiles": names, "source_ids": [2_000_000_000]},
                                headers=auth(seeded["wa"], "compare"))
    assert foreign.status_code == unknown.status_code == 200
    for r in (foreign, unknown):
        assert all(p["results"] == [] for p in r.json()["profile_results"]) and r.json()["alignment"]["rows"] == []
        assert "BRAVO-ONLY-SECRET-4821" not in r.text.replace("Bravo launch codename BRAVO-ONLY-SECRET-4821", "")


async def test_a_token_for_workspace_b_does_not_see_workspace_a(client, seeded):
    """The mirror image: the only thing that selects the tenant is the token."""
    fid_a = seeded["a_ids"]["ignore-previous.md"]
    r = await client.post("/retrieve", json=with_ws({"query": "warranty period Atlas pump", "profile": HYBRID,
                                                     "source_ids": [fid_a]}, seeded["wa"]),
                          headers=auth(seeded["wb"], "retrieve"))
    assert r.status_code == 200 and r.json()["candidates"] == []


# ------------------------------------------------------------ the SQL functions behind the Next.js routes

def test_soft_delete_function_refuses_a_foreign_workspace_or_user(seeded):
    fid = next(iter(seeded["b_ids"].values()))
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        owner = c.execute("select user_id from files where id=%s", (fid,)).fetchone()[0]
        assert c.execute("select scribe_soft_delete_source(%s,%s,%s)", (seeded["wa"], owner, fid)).fetchone()[0] is False
        assert c.execute("select scribe_soft_delete_source(%s,%s,%s)", (seeded["wb"], "mallory", fid)).fetchone()[0] is False
        assert c.execute("select deleted_at from files where id=%s", (fid,)).fetchone()[0] is None


def test_reserve_version_function_refuses_a_foreign_workspace_or_user(seeded):
    fid = next(iter(seeded["b_ids"].values()))
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        owner, mime = c.execute("select user_id, type from files where id=%s", (fid,)).fetchone()
        for ws, user in ((seeded["wa"], owner), (seeded["wb"], "mallory")):
            with pytest.raises(psycopg.errors.NoDataFound, match="SOURCE_NOT_FOUND"):
                c.execute("select * from scribe_reserve_source_version(%s,%s,%s,%s,%s)", (ws, fid, user, mime, 10))
        assert c.execute("select count(*) from source_versions where source_id=%s", (fid,)).fetchone()[0] == 1

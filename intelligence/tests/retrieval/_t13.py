"""T13 test helpers: seed two workspaces through the REAL indexer + FakeEmbedder.

FakeEmbedder vectors carry no meaning, so dense tests plant embeddings: `plant` stores the
embedding of an exact query text on a chunk, which makes that chunk the (distance 0) nearest
neighbour of that query. That is how the leak tests prove that a forbidden chunk would win if the
scoping filters were missing."""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
from _db import aconnect, new_file, new_workspace  # noqa: E402

from scribe_ingest.embedding import FakeEmbedder  # noqa: E402
from scribe_ingest.chunking import STRUCTURAL_V1, chunk_document  # noqa: E402
from scribe_ingest.indexer import (build_generation, embed_generation, ensure_generation, load_index_profile,  # noqa: E402
                                   parse_for_index, persist_chunks, register_version)

MD = "text/markdown"

API = ("# API errors\n\nRevoked keys return the error code ERR_AUTH_403 and the fix ticket is INV-2047.\n\n"
       "Cold goods (SKU NW-COLD-04) must be stored between 2 and 8 degrees.\n").encode()
HR = b"# Leave\n\nEmployees take annual leave of 25 days per year.\n"
SCATTER = b"# Annual report\n\nThe annual budget grew. Contractors may leave at any time.\n"
STEM = b"# Policy\n\nA team member may take leave when needed.\n"
OLD_V1 = b"# Retired\n\nThe ZEBRAFISH_OLD marker belongs to the retired protocol.\n"
OLD_V2 = b"# Replacement\n\nThis text is about giraffes and nothing else.\n"
GONE = b"# Plans\n\nThe quokka migration plan is secret.\n"
FILLERS = [f"# Notes {i}\n\nGeneral notes number {i} about error codes, tickets and routine matters.\n".encode()
           for i in range(5)]
BUILDING = b"# Draft\n\nThe AARDVARK_BUILDING draft was embedded but never activated.\n"
B_ONLY = b"# Sanctuary\n\nThe platypus sanctuary expands in spring. Zebrafish quokka migration ERR_AUTH_403.\n"

LEAK_QUERIES = ["ERR_AUTH_403", "INV-2047", "annual leave", "leaves", "quokka migration", "ZEBRAFISH_OLD",
                "platypus sanctuary", "error codes ticket", "NW-COLD-04", "giraffes", "AARDVARK_BUILDING"]


async def ingest(conn, wid: str, name: str, data: bytes, embedder, profile=None, mime: str = MD) -> dict:
    fid = await new_file(conn, wid, name, mime)
    ver = await register_version(conn, wid, fid, data, mime, created_by="t13")
    res = await build_generation(conn, wid, fid, ver, data, mime, embedder, name=name, profile=profile)
    assert res.status == "ACTIVE", (name, res.error_code, res.message)
    return {"id": fid, "name": name, "gen": res.generation_id, "version": ver.id}


async def build_unactivated(conn, wid: str, name: str, data: bytes, embedder, profile) -> dict:
    """PARSE+CHUNK+EMBED a version up to READY but never ACTIVATE it (a generation under construction)."""
    fid = await new_file(conn, wid, name, MD)
    ver = await register_version(conn, wid, fid, data, MD, created_by="t13")
    gen, _ = await ensure_generation(conn, wid, fid, ver.id, profile)
    doc = parse_for_index(data, MD, name)
    await persist_chunks(conn, wid, fid, ver.id, gen, doc, chunk_document(doc, STRUCTURAL_V1))
    await embed_generation(conn, wid, fid, ver.id, gen, profile, embedder)
    cur = await conn.execute("select status from index_generations where id=%s", (gen,))
    assert (await cur.fetchone())[0] == "READY"
    return {"id": fid, "name": name, "gen": gen, "version": ver.id}


async def reingest(conn, wid: str, fid: int, name: str, data: bytes, embedder, mime: str = MD):
    ver = await register_version(conn, wid, fid, data, mime, created_by="t13")
    res = await build_generation(conn, wid, fid, ver, data, mime, embedder, name=name)
    assert res.status == "ACTIVE", (res.error_code, res.message)
    return res


async def chunk_ids(conn, gen) -> list[uuid.UUID]:
    cur = await conn.execute("select id from chunks where generation_id=%s and parent_id is not null", (gen,))
    return [r[0] for r in await cur.fetchall()]


async def structural_profile_id(conn) -> uuid.UUID:
    return (await load_index_profile(conn)).id


async def plant(conn, ids, wid_of: dict, text: str, embedder: FakeEmbedder) -> None:
    """Give every chunk in `ids` the exact query embedding of `text`."""
    from scribe_intel.retrieval.queries import vec_literal

    (vec,) = await embedder.embed_queries([text])
    for cid in ids:
        await conn.execute(
            "insert into chunk_embeddings_1536(chunk_id, workspace_id, embedding_profile_id, embedding) "
            "select c.id, c.workspace_id, (select embedding_profile_id from index_profiles where name='structural-v1'), "
            "%s::vector from chunks c where c.id=%s "
            "on conflict (chunk_id) do update set embedding = excluded.embedding", (vec_literal(vec), cid))


async def seed(url: str) -> dict:
    """Two workspaces + inactive/deleted/foreign data. Returns ids."""
    conn = await aconnect(url)
    emb = FakeEmbedder()
    try:
        wa, wb = await new_workspace(conn), await new_workspace(conn)
        profile = await load_index_profile(conn)
        a: dict[str, dict] = {}
        for name, data in [("api-errors.md", API), ("hr.md", HR), ("scatter.md", SCATTER), ("stem.md", STEM),
                           ("gone.md", GONE)] + [(f"filler-{i}.md", d) for i, d in enumerate(FILLERS)]:
            a[name] = await ingest(conn, wa, name, data, emb, profile)
        a["old.md"] = await ingest(conn, wa, "old.md", OLD_V1, emb, profile)
        old_chunks = await chunk_ids(conn, a["old.md"]["gen"])
        await reingest(conn, wa, a["old.md"]["id"], "old.md", OLD_V2, emb)
        a["building.md"] = await build_unactivated(conn, wa, "building.md", BUILDING, emb, profile)
        await conn.execute("update files set deleted_at = now() where id=%s", (a["gone.md"]["id"],))
        b: dict[str, dict] = {}
        for name, data in [("api-errors.md", API), ("hr.md", HR), ("scatter.md", SCATTER), ("stem.md", STEM),
                           ("gone.md", GONE), ("old.md", OLD_V1), ("b-only.md", B_ONLY)]:
            b[name] = await ingest(conn, wb, name, data, emb, profile)
        return {
            "url": url, "wa": wa, "wb": wb, "a": a, "b": b, "profile_id": profile.id,
            "a_ids": {n: x["id"] for n, x in a.items()}, "b_ids": {n: x["id"] for n, x in b.items()},
            "superseded_chunks": old_chunks,
            "deleted_chunks": await chunk_ids(conn, a["gone.md"]["gen"]),
            "building_chunks": await chunk_ids(conn, a["building.md"]["gen"]),
            "b_chunks": [c for x in b.values() for c in await chunk_ids(conn, x["gen"])],
        }
    finally:
        await conn.close()


def seed_sync(url: str) -> dict:
    return asyncio.run(seed(url))


async def conn_for(url: str) -> psycopg.AsyncConnection:
    return await aconnect(url)


_corpus_cache: dict[str, dict] = {}


def corpus_workspace(url: str) -> dict:
    """Whole eval corpus ingested once per process with the FAKE embedder (ingest-corpus logic)."""
    if url not in _corpus_cache:
        from _db import CORPUS
        from scribe_eval.ingest_corpus import ingest_corpus, list_corpus

        async def go() -> dict:
            conn = await aconnect(url)
            try:
                wid = await new_workspace(conn)
                res = await ingest_corpus(conn, wid, list_corpus(CORPUS), lambda n: (CORPUS / n).read_bytes(),
                                          FakeEmbedder())
                return {"wid": wid, "results": dict(res), "profile_id": await structural_profile_id(conn)}
            finally:
                await conn.close()

        _corpus_cache[url] = asyncio.run(go())
    return _corpus_cache[url]


LEGACY_PROFILE = "10000000-0000-4000-8000-000000000001"
LEGACY_EMBEDDING = "e0000000-0000-4000-8000-000000000001"


def unit3072(sim: float, axis: int, scale: float = 1.0) -> list[float]:
    """3072-d vector whose cosine similarity with e0 is exactly `sim` (axis >= 1 carries the rest)."""
    import math

    v = [0.0] * 3072
    v[0] = sim * scale
    v[axis] = math.sqrt(max(0.0, 1 - sim * sim)) * scale
    return v


async def seed_legacy(conn, wid: str, name: str, sims: list[float], *, deleted: bool = False,
                      scale: float = 1.0) -> list[uuid.UUID]:
    """A legacy-v1 source: ACTIVE generation, flat chunks, 3072-d embeddings with the given cosine
    similarity to the unit query e0."""
    from scribe_intel.retrieval.queries import vec_literal

    fid = await new_file(conn, wid, name, "text/plain")
    cur = await conn.execute(
        "insert into source_versions(source_id, workspace_id, version_number, sha256, upload_state) "
        "values (%s,%s,1,%s,'stored') returning id", (fid, wid, uuid.uuid4().hex))
    ver = (await cur.fetchone())[0]
    cur = await conn.execute(
        "insert into index_generations(source_id, source_version_id, index_profile_id, workspace_id, status, "
        "expected_chunks, embedded_chunks) values (%s,%s,%s,%s,'ACTIVE',%s,%s) returning id",
        (fid, ver, LEGACY_PROFILE, wid, len(sims), len(sims)))
    gen = (await cur.fetchone())[0]
    await conn.execute("insert into active_indexes(source_id, index_profile_id, generation_id, workspace_id) "
                       "values (%s,%s,%s,%s)", (fid, LEGACY_PROFILE, gen, wid))
    ids = []
    for i, sim in enumerate(sims):
        cur = await conn.execute(
            "insert into chunks(generation_id, source_version_id, workspace_id, chunk_index, content, content_hash) "
            "values (%s,%s,%s,%s,%s,%s) returning id", (gen, ver, wid, i, f"{name} chunk {i} sim {sim}", uuid.uuid4().hex))
        cid = (await cur.fetchone())[0]
        ids.append(cid)
        await conn.execute(
            "insert into chunk_embeddings_3072(chunk_id, workspace_id, embedding_profile_id, embedding) "
            "values (%s,%s,%s,%s::vector)", (cid, wid, LEGACY_EMBEDDING, vec_literal(unit3072(sim, 1 + i % 3000, scale))))
    if deleted:
        await conn.execute("update files set deleted_at = now() where id=%s", (fid,))
    return ids

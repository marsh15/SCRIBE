"""Seed helpers for T22 tests: a backfilled legacy Source (version 1, ACTIVE legacy-v1 generation, 3072-dim embedding)."""
from __future__ import annotations

VEC3072 = "[" + ",".join(["0.01"] * 3072) + "]"
VEC1536 = "[" + ",".join(["0.01"] * 1536) + "]"


async def profile_id(conn, name: str):
    return (await (await conn.execute("select id from index_profiles where name=%s", (name,))).fetchone())[0]


async def legacy_source(conn, wid: str, *, name="legacy.md", storage_url: str | None = "https://blob.example/legacy.md",
                        mime="text/markdown") -> int:
    """What scripts/backfill-legacy-generations.ts leaves behind for one source."""
    cur = await conn.execute(
        "insert into files(name,type,size,user_id,workspace_id,status,storage_url,storage_key) "
        "values (%s,%s,10,'t22',%s,'ready',%s,%s) returning id", (name, mime, wid, storage_url, "legacy/" + name))
    fid = (await cur.fetchone())[0]
    cur = await conn.execute(
        "insert into source_versions(source_id,workspace_id,version_number,storage_key,sha256,size_bytes,mime_type,"
        "upload_state,created_by) values (%s,%s,1,%s,NULL,10,%s,'stored','t22') returning id",
        (fid, wid, "legacy/" + name, mime))
    vid = (await cur.fetchone())[0]
    legacy = await profile_id(conn, "legacy-v1")
    cur = await conn.execute(
        "insert into index_generations(source_id,source_version_id,index_profile_id,workspace_id,status,"
        "expected_chunks,embedded_chunks,activated_at) values (%s,%s,%s,%s,'ACTIVE',1,1,now()) returning id",
        (fid, vid, legacy, wid))
    gid = (await cur.fetchone())[0]
    cur = await conn.execute(
        "insert into chunks(generation_id,source_version_id,workspace_id,chunk_index,content,content_hash) "
        "values (%s,%s,%s,0,'legacy text','h-legacy') returning id", (gid, vid, wid))
    cid = (await cur.fetchone())[0]
    await conn.execute(
        "insert into chunk_embeddings_3072(chunk_id,workspace_id,embedding_profile_id,embedding) "
        "select %s,%s,ip.embedding_profile_id,%s::vector from index_profiles ip where ip.id=%s",
        (cid, wid, VEC3072, legacy))
    await conn.execute("insert into active_indexes(source_id,index_profile_id,generation_id,workspace_id) "
                       "values (%s,%s,%s,%s)", (fid, legacy, gid, wid))
    await conn.execute("update files set desired_version_id=%s, active_version_id=%s where id=%s", (vid, vid, fid))
    return fid


async def add_structural(conn, wid: str, fid: int, *, status="ACTIVE", expected=1, embedded=1) -> None:
    """A structural-v1 generation on the Source's active version (one child chunk + 1536-dim embedding)."""
    vid, = await (await conn.execute("select active_version_id from files where id=%s", (fid,))).fetchone()
    sp = await profile_id(conn, "structural-v1")
    cur = await conn.execute(
        "insert into index_generations(source_id,source_version_id,index_profile_id,workspace_id,status,"
        "expected_chunks,embedded_chunks,activated_at) values (%s,%s,%s,%s,%s,%s,%s,now()) returning id",
        (fid, vid, sp, wid, status, expected, embedded))
    gid = (await cur.fetchone())[0]
    cur = await conn.execute(
        "insert into chunks(generation_id,source_version_id,workspace_id,chunk_index,content,content_hash) "
        "values (%s,%s,%s,0,'structural text','h-struct') returning id", (gid, vid, wid))
    cid = (await cur.fetchone())[0]
    await conn.execute(
        "insert into chunk_embeddings_1536(chunk_id,workspace_id,embedding_profile_id,embedding) "
        "select %s,%s,ip.embedding_profile_id,%s::vector from index_profiles ip where ip.id=%s",
        (cid, wid, VEC1536, sp))

"""T14: parent loading is scoped exactly like the child retrievers and snapshot-consistent."""
from __future__ import annotations

import uuid

import pytest
from _t13 import conn_for, ingest, reingest, seed_sync, structural_profile_id  # noqa: F401  (sets sys.path)
from _db import new_workspace
from conftest import DATABASE_URL_TEST, needs_db
from scribe_ingest.embedding import FakeEmbedder
from scribe_intel.retrieval.packing import ParentContent
from scribe_intel.retrieval.parents import load_parents
from scribe_intel.retrieval.search import snapshot_on

pytestmark = needs_db
EMB = FakeEmbedder()
V1 = b"# Alpha\n\nThe OLDTOKEN belongs to the first version of this note.\n"
V2 = b"# Beta\n\nThe NEWTOKEN belongs to the second version of this note.\n"


@pytest.fixture(scope="module")
def seeded():
    return seed_sync(DATABASE_URL_TEST)


@pytest.fixture()
async def conn():
    c = await conn_for(DATABASE_URL_TEST)
    yield c
    await c.close()


async def parents_of(conn, gen) -> dict[uuid.UUID, ParentContent]:
    cur = await conn.execute("select id, content, heading_text from chunks where generation_id=%s and parent_id is null",
                             (gen,))
    return {r[0]: ParentContent(r[1], r[2]) for r in await cur.fetchall()}


async def parent_ids_of_children(conn, child_ids) -> list[uuid.UUID]:
    cur = await conn.execute("select distinct parent_id from chunks where id = any(%s) and parent_id is not null",
                             (list(child_ids),))
    return [r[0] for r in await cur.fetchall()]


async def load(conn, wid, prof, ids):
    await snapshot_on(conn, wid)
    try:
        return await load_parents(conn, wid, prof, ids)
    finally:
        await conn.execute("rollback")


async def test_active_parents_load_with_exact_content_and_heading(conn, seeded):
    prof = await structural_profile_id(conn)
    expected = await parents_of(conn, seeded["a"]["api-errors.md"]["gen"])
    assert expected and all(p.content for p in expected.values())
    got = await load(conn, seeded["wa"], prof, list(expected))
    assert got == expected
    assert any(p.heading_text for p in got.values())
    # duplicates in the request are harmless
    ids = list(expected)
    assert await load(conn, seeded["wa"], prof, ids + ids) == expected


async def test_empty_ids_returns_empty_without_a_query(seeded):
    class Boom:
        async def execute(self, *a, **k):
            raise AssertionError("no query expected")

    assert await load_parents(Boom(), seeded["wa"], uuid.uuid4(), []) == {}


async def test_foreign_superseded_deleted_and_child_ids_return_nothing(conn, seeded):
    prof = await structural_profile_id(conn)
    wa, wb = seeded["wa"], seeded["wb"]
    a_parents = await parents_of(conn, seeded["a"]["api-errors.md"]["gen"])
    b_parents = await parents_of(conn, seeded["b"]["api-errors.md"]["gen"])
    superseded = await parent_ids_of_children(conn, seeded["superseded_chunks"])
    deleted = await parent_ids_of_children(conn, seeded["deleted_chunks"])
    building = await parent_ids_of_children(conn, seeded["building_chunks"])
    assert a_parents and b_parents and superseded and deleted and building
    # sanity: the rows exist, they are just out of scope
    cur = await conn.execute("select count(*) from chunks where id = any(%s) and parent_id is null",
                             (superseded + deleted + building,))
    assert (await cur.fetchone())[0] == len(superseded + deleted + building)

    assert await load(conn, wa, prof, list(b_parents)) == {}  # another workspace's parents
    assert await load(conn, wb, prof, list(a_parents)) == {}
    assert await load(conn, wa, prof, superseded) == {}  # superseded generation
    assert await load(conn, wa, prof, deleted) == {}  # soft-deleted file
    assert await load(conn, wa, prof, building) == {}  # generation never activated
    assert await load(conn, wa, prof, seeded["b_chunks"][:3]) == {}  # foreign child ids
    children = [c for n in ("api-errors.md", "hr.md") for c in await _children(conn, seeded["a"][n]["gen"])]
    assert children and await load(conn, wa, prof, children) == {}  # child ids are never parents
    assert await load(conn, wa, prof, [uuid.uuid4()]) == {}
    # mixed request: only the in-scope parents come back
    mixed = await load(conn, wa, prof, list(a_parents) + list(b_parents) + superseded + deleted)
    assert mixed == a_parents


async def _children(conn, gen):
    cur = await conn.execute("select id from chunks where generation_id=%s and parent_id is not null", (gen,))
    return [r[0] for r in await cur.fetchall()]


async def test_parents_come_from_the_open_snapshot(conn):
    wid = await new_workspace(conn)
    f = await ingest(conn, wid, "note.md", V1, EMB)
    prof = await structural_profile_id(conn)
    old = await parents_of(conn, f["gen"])
    assert old and any("OLDTOKEN" in p.content for p in old.values())
    other = await conn_for(DATABASE_URL_TEST)
    snap_conn = await conn_for(DATABASE_URL_TEST)
    try:
        await snapshot_on(snap_conn, wid)  # snapshot fixed here
        res = await reingest(other, wid, f["id"], "note.md", V2, EMB)  # activates generation 2 mid-snapshot
        new = await parents_of(conn, res.generation_id)
        assert new and res.generation_id != f["gen"]
        assert await load_parents(snap_conn, wid, prof, list(old) + list(new)) == old  # old snapshot: old parents only
        await snap_conn.execute("rollback")
        await snapshot_on(snap_conn, wid)  # a fresh snapshot sees the new generation only
        assert await load_parents(snap_conn, wid, prof, list(old) + list(new)) == new
    finally:
        await snap_conn.execute("rollback")
        await snap_conn.close()
        await other.close()

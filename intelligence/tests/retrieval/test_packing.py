import pytest
from hypothesis import given, settings, strategies as st

from _t14_factories import cand, cid, pid, span
from scribe_intel.retrieval.dedup import dedup
from scribe_intel.retrieval.fusion import rrf_fuse
from scribe_intel.retrieval.packing import ParentContent, expand_parents, pack, to_entries
from scribe_intel.retrieval.types import PackedItem
from _t14_factories import SV

wc = lambda t: len(t.split())  # noqa: E731


def D(*cs, merge=True):
    return dedup(rrf_fuse({"dense": list(cs)}), merge_parents=merge)


def test_greedy_stops_at_first_overflow_no_skip_and_fill():
    cs = [cand(1, 1, content="a " * 40), cand(2, 2, content="b " * 80), cand(3, 3, content="c " * 5)]
    ctx = pack(to_entries(D(*cs), wc), budget_tokens=100, count_tokens=wc)
    assert [e.item.chunk_id for e in ctx.entries] == [cid(1)]
    assert ctx.used_tokens == 40 and ctx.budget_tokens == 100
    assert [(d.chunk_id, d.reason) for d in ctx.dropped] == [(cid(2), "exceeds_budget"), (cid(3), "after_budget_stop")]


def test_first_item_larger_than_budget_gives_empty_context():
    ctx = pack(to_entries(D(cand(1, 1, content="a " * 50)), wc), 10, wc)
    assert ctx.entries == [] and ctx.used_tokens == 0 and len(ctx.dropped) == 1


def test_heading_counts_and_table_header_passes_through():
    table = "| h1 | h2 |\n|---|---|\n| 1 | 2 |"
    c = cand(1, 1, content=table, heading="Results")
    ctx = pack(to_entries(D(c), wc), 100, wc)
    it = ctx.entries[0].item
    assert it.text == table and it.token_count == wc(table) + 1


@given(st.lists(st.integers(1, 50), max_size=15), st.integers(0, 200))
@settings(max_examples=200)
def test_never_exceeds_budget_and_monotone(sizes, budget):
    cs = [cand(i, i + 1, content=f"w{i} " * s) for i, s in enumerate(sizes)]
    entries = to_entries(D(*cs), wc)
    small = pack(entries, budget, wc)
    big = pack(to_entries(D(*cs), wc), budget + 25, wc)
    assert small.used_tokens <= budget and big.used_tokens <= budget + 25
    ids_small = [e.item.chunk_id for e in small.entries]
    ids_big = [e.item.chunk_id for e in big.entries]
    assert ids_big[: len(ids_small)] == ids_small  # prefix: more budget never loses items
    assert len(small.entries) + len(small.dropped) == len(sizes)


async def test_parent_expansion_keeps_child_spans_and_merges_siblings():
    a = cand(1, 1, parent=1, content="child one", spans=[span(1, 0, 5)], heading="Child H")
    b = cand(2, 2, parent=1, content="child two", spans=[span(2, 0, 5)])
    async def loader(ids):
        assert ids == [pid(1)]
        return {pid(1): ParentContent("the whole parent section text", "Parent H")}
    entries = await expand_parents(D(a, b), loader, budget_tokens=100, count_tokens=wc)
    assert len(entries) == 1
    it = entries[0].item
    assert it.expanded_from_parent and it.text == "the whole parent section text" and it.heading_text == "Parent H"
    assert [s.block_index for s in it.spans] == [1, 2]  # CHILD spans, not parent
    assert entries[0].merged_chunk_ids == [cid(2)] and it.chunk_id == cid(1)


async def test_parent_too_big_or_missing_keeps_child_and_unmerges_siblings():
    a = cand(1, 1, parent=1, content="child one")
    b = cand(2, 2, parent=1, content="child two")
    async def big(ids):
        return {pid(1): "p " * 60}  # 60 > 100*0.5
    entries = await expand_parents(D(a, b), big, budget_tokens=100, count_tokens=wc)
    assert [e.item.chunk_id for e in entries] == [cid(1), cid(2)]
    assert not any(e.item.expanded_from_parent for e in entries)
    assert entries[0].item.text == "child one"

    async def none(ids):
        return {}
    entries = await expand_parents(D(a, b), none, budget_tokens=100, count_tokens=wc)
    assert [e.item.chunk_id for e in entries] == [cid(1), cid(2)]


async def test_expansion_respects_running_budget():
    cs = [cand(i, i, parent=i, content=f"c{i}") for i in (1, 2, 3)]
    async def loader(ids):
        return {pid(i): "p " * 40 for i in (1, 2, 3)}
    entries = await expand_parents(D(*cs), loader, budget_tokens=100, count_tokens=wc)
    assert [e.item.expanded_from_parent for e in entries] == [True, True, False]
    assert pack(entries, 100, wc).used_tokens <= 100


async def test_expand_dedup_provenance_carried():
    a = cand(1, 1, content="same", doc="a")
    b = cand(2, 2, content="same", doc="b")
    async def loader(ids):
        return {}
    e = (await expand_parents(D(a, b), loader, count_tokens=wc))[0]
    assert e.duplicates == [cid(2)] and e.also_in[0].doc_name == "b"


def test_pack_accepts_bare_packed_items():
    it = PackedItem(cid(1), None, "x y", None, "d", 1, SV, [], 0, None)
    assert pack([it], 5, wc).used_tokens == 2

import pytest

from _t14_factories import cand, cid, pid
from scribe_intel.retrieval import profiles as P
from scribe_intel.retrieval.pipeline import run_pipeline
from scribe_intel.retrieval.rerank import FakeReranker, RerankError

wc = lambda t: len(t.split())  # noqa: E731


def corpus():
    dense = [cand(1, 1, "dense", content="apples are red fruit", parent=1),
             cand(2, 2, "dense", content="bananas are yellow", parent=1),
             cand(3, 3, "dense", content="cherry red tart", doc="b.pdf"),
             cand(4, 4, "dense", content="unrelated filler words")]
    lexical = [cand(3, 1, "lexical", content="cherry red tart", doc="b.pdf"),
               cand(5, 2, "lexical", content="cherry red tart", doc="c.pdf"),  # same text, other doc
               cand(1, 3, "lexical", content="apples are red fruit", parent=1)]
    return {"dense": dense, "lexical": lexical}


async def loader(ids):
    return {pid(1): "apples and bananas parent section"}


async def run(profile, reranker=None, ldr=loader, retrieved=None):
    return await run_pipeline(profile, "red cherry", retrieved or corpus(), reranker=reranker or FakeReranker(),
                              parent_loader=ldr, count_tokens=wc)


async def test_profile_a_dense_only():
    r = await run(P.PROFILE_A)
    assert [e.item.chunk_id for e in r.packed.entries] == [cid(1), cid(2), cid(3), cid(4)]
    assert r.degraded == [] and r.answerability_inputs == {"top_rerank_score": None}
    assert r.counts["dense"] == 4 and "lexical" not in r.counts
    assert all(not e.item.expanded_from_parent for e in r.packed.entries)


async def test_profile_b_lexical_only_dedups_hash_dup():
    r = await run(P.PROFILE_B)
    ids = [e.item.chunk_id for e in r.packed.entries]
    assert ids == [cid(3), cid(1)]  # chunk 5 is a content duplicate of 3
    assert r.packed.entries[0].duplicates == [cid(5)] and r.packed.entries[0].also_in[0].doc_name == "c.pdf"


async def test_profile_c_hybrid_rrf():
    r = await run(P.PROFILE_C)
    # 3: dense3 + lex1 ; 1: dense1 + lex3 -> 3 = 1/63+1/61 = .03226 , 1 = 1/61+1/63 equal -> best rank equal (1) -> id
    assert r.counts["fused"] == 5 and r.ranked[0].fusion_rank == 1
    assert all(x.rerank_rank is None for x in r.ranked)
    assert r.answerability_inputs["top_rerank_score"] is None


async def test_profile_d_dense_rerank():
    r = await run(P.PROFILE_D)
    assert r.ranked[0].rerank_rank == 1
    assert r.answerability_inputs["top_rerank_score"] == 1.0
    assert r.packed.entries[0].item.chunk_id == cid(3)  # "cherry red tart" matches both query tokens
    assert r.packed.entries[0].item.rerank_score == 1.0


async def test_profile_e_full_pipeline():
    r = await run(P.PROFILE_E)
    assert r.degraded == []
    first = r.packed.entries[0]
    assert first.item.chunk_id == cid(3) and first.duplicates == [cid(5)]
    expanded = [e for e in r.packed.entries if e.item.expanded_from_parent]
    assert len(expanded) == 1 and expanded[0].item.text == "apples and bananas parent section"
    assert expanded[0].merged_chunk_ids  # sibling 2 folded into parent entry
    assert set(r.stage_ms) == {"fusion", "rerank", "dedup", "expand", "pack"}
    assert r.packed.used_tokens <= 6000
    selected = {x.chunk_id for x in r.ranked if x.selected}
    assert cid(3) in selected and cid(2) in selected and cid(4) in selected


async def test_degraded_rerank_uses_fused_order_and_is_labelled():
    r = await run(P.PROFILE_E, reranker=FakeReranker(fail_with=RerankError("RATE_LIMITED", "x", retryable=True)))
    assert r.degraded == ["rerank_unavailable"]
    assert r.answerability_inputs["top_rerank_score"] is None
    assert [x.fusion_rank for x in r.ranked] == sorted(x.fusion_rank for x in r.ranked)
    assert r.packed.entries  # still produces context


async def test_rerank_profile_without_reranker_is_degraded():
    from scribe_intel.retrieval.pipeline import run_pipeline as rp
    r = await rp(P.PROFILE_D, "q", corpus(), reranker=None, parent_loader=None, count_tokens=wc)
    assert r.degraded == ["rerank_unavailable"] and r.packed.entries


async def test_parent_loader_failure_degrades_but_succeeds():
    async def boom(ids):
        raise RuntimeError("db down")
    r = await run(P.PROFILE_E, ldr=boom)
    assert r.degraded == ["parent_expansion_unavailable"]
    assert not any(e.item.expanded_from_parent for e in r.packed.entries)


async def test_top_k_limits_and_budget_enforced():
    big = {"dense": [cand(i, i, "dense", content=f"w{i} " + "word " * 399) for i in range(1, 50)]}
    r = await run(P.PROFILE_A, retrieved=big)
    assert r.counts["dense"] == 40
    assert r.packed.used_tokens <= 6000 and len(r.packed.entries) == 6000 // 400
    assert r.counts["dropped"] == 40 - 15


async def test_empty_retrieval():
    r = await run(P.PROFILE_E, retrieved={"dense": [], "lexical": []})
    assert r.packed.entries == [] and r.answerability_inputs["top_rerank_score"] is None

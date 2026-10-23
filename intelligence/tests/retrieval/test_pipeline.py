import pytest

from _t14_factories import cand, cid, pid
from scribe_intel.retrieval import profiles as P
from scribe_intel.retrieval.context import CharacterSpan
from scribe_intel.retrieval.pipeline import run_pipeline
from scribe_intel.retrieval.rerank import FakeReranker, RerankError
from scribe_ingest.tokens import count_tokens

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


async def test_hash_duplicate_of_a_packed_chunk_is_marked_selected():
    r = await run(P.PROFILE_B)
    assert r.packed.entries[0].duplicates == [cid(5)]
    sel = {x.chunk_id: x.selected for x in r.ranked}
    assert sel[cid(3)] and sel[cid(5)] and sel[cid(1)]
    # a duplicate whose kept twin did not make it into the context stays unselected
    tight = P.RetrievalProfile(name="tight", description="t", config=P.ProfileConfig(
        lexical_top_k=40, budget_tokens=3))
    r = await run(tight)
    assert [e.item.chunk_id for e in r.packed.entries] == [cid(3)]
    sel = {x.chunk_id: x.selected for x in r.ranked}
    assert sel[cid(3)] and sel[cid(5)] and not sel[cid(1)]


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
    assert r.packed.context_strategy is None
    selected = {x.chunk_id for x in r.ranked if x.selected}
    assert cid(3) in selected and cid(2) in selected and cid(4) in selected


async def test_degraded_rerank_uses_fused_order_and_is_labelled():
    r = await run(P.PROFILE_E, reranker=FakeReranker(fail_with=RerankError("RATE_LIMITED", "x", retryable=True)))
    assert r.degraded == ["rerank_unavailable"] and r.extra == {"rerank_error": "RATE_LIMITED"}
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


async def test_context_strategies_apply_candidate_limits_and_parent_expansion():
    from scribe_intel.retrieval.pipeline import run_pipeline

    retrieved = {"dense": [cand(i, i, "dense", content=f"candidate {i} matches query", parent=i)
                            for i in range(1, 26)]}
    top20 = await run_pipeline(P.PROFILE_TOP20_FUSED, "query", retrieved, reranker=None, parent_loader=None,
                              count_tokens=wc)
    top5 = await run_pipeline(P.PROFILE_TOP5_RERANKED, "query", retrieved, reranker=FakeReranker(),
                              parent_loader=None, count_tokens=wc)

    async def parents(ids):
        return {parent: f"parent section {parent}" for parent in ids}

    top5_parent = await run_pipeline(P.PROFILE_TOP5_PARENT, "query", retrieved, reranker=FakeReranker(),
                                     parent_loader=parents, count_tokens=wc)

    assert len(top20.packed.entries) == 20
    assert top20.packed.context_strategy == "top20_fused"
    assert [entry.item.chunk_id for entry in top20.packed.entries] == [
        candidate.chunk_id for candidate in top20.ranked[:20]
    ]
    assert all(item.rerank_rank is None for item in top20.ranked)
    assert all(entry.item.token_count == wc(entry.item.text) for entry in top20.packed.entries)
    assert len(top5.packed.entries) == 5
    assert top5.packed.context_strategy == "top5_reranked"
    assert all(item.rerank_rank is not None for item in top5.ranked if item.selected)
    assert all(entry.item.token_count == wc(entry.item.text) for entry in top5.packed.entries)
    assert len(top5_parent.packed.entries) == 5
    assert top5_parent.packed.context_strategy == "top5_parent"
    assert all(entry.item.expanded_from_parent for entry in top5_parent.packed.entries)
    assert all(entry.item.token_count == wc(entry.item.text) for entry in top5_parent.packed.entries)


async def test_compressed_strategy_keeps_query_sentence_and_records_offsets():
    from scribe_intel.retrieval.pipeline import run_pipeline

    original = "First section is unrelated. Query describes the red cherry orchard. Final section is unrelated."
    profile = P.RetrievalProfile(
        name="compressed-small", description="test", config=P.PROFILE_COMPRESSED.config.model_copy(
            update={"budget_tokens": 30},
        ),
    )
    result = await run_pipeline(profile, "red cherry", {"dense": [cand(1, 1, "dense", content=original)]},
                                reranker=FakeReranker(), parent_loader=None, count_tokens=wc)

    [entry] = result.packed.entries
    expected = "Query describes the red cherry orchard."
    assert entry.item.text == expected and entry.item.token_count == wc(expected)
    assert result.packed.context_strategy == "compressed"
    assert original[entry.kept_spans[0].start:entry.kept_spans[0].end] == expected
    assert entry.dropped_spans
    assert result.ranked[0].candidate.content == original


async def test_default_compressed_profile_reduces_realistic_child_chunks():
    query = "annual leave payment deadline"

    def realistic_chunk(source_id: int) -> str:
        sentences = ["The annual leave payment deadline is 30 June for eligible employees."]
        index = 1
        while count_tokens(" ".join(sentences)) < 320:
            sentences.append(
                f"Clause {index} describes a standard administrative review process for employees in policy {source_id}."
            )
            index += 1
        content = " ".join(sentences)
        assert 300 <= count_tokens(content) <= 384
        return content

    candidates = [cand(i, i, "dense", doc=f"policy-{i}.pdf", content=realistic_chunk(i)) for i in range(1, 6)]
    retrieved = {"dense": candidates}
    baseline = await run_pipeline(P.PROFILE_TOP5_RERANKED, query, retrieved, reranker=FakeReranker(),
                                  parent_loader=None, count_tokens=count_tokens)
    compressed = await run_pipeline(P.PROFILE_COMPRESSED, query, retrieved, reranker=FakeReranker(),
                                    parent_loader=None, count_tokens=count_tokens)
    source_by_id = {candidate.chunk_id: candidate.content for candidate in candidates}

    assert len(baseline.packed.entries) == len(compressed.packed.entries) == 5
    baseline_tokens = sum(entry.item.token_count for entry in baseline.packed.entries)
    compressed_tokens = sum(entry.item.token_count for entry in compressed.packed.entries)
    assert compressed_tokens < baseline_tokens
    assert compressed.packed.budget_tokens == P.PROFILE_COMPRESSED.config.budget_tokens == 1000
    assert compressed.packed.used_tokens <= 1000
    for entry in compressed.packed.entries:
        source = source_by_id[entry.item.chunk_id]
        assert entry.dropped_spans
        assert entry.item.text != source
        pieces = [source[span.start:span.end] for span in entry.kept_spans]
        assert pieces and entry.item.text == "\n\n".join(pieces)
        assert all(piece in source for piece in pieces)
        assert entry.item.token_count <= 200


async def test_compression_omits_oversized_sentence_and_records_full_drop_span():
    from scribe_intel.retrieve_routes import context_json
    from scribe_intel.retrieval.pipeline import run_pipeline

    oversized = "red " * 200 + "red."
    remaining = "The red cherry policy is current."
    result = await run_pipeline(
        P.PROFILE_COMPRESSED, "red cherry",
        {"dense": [cand(31, 1, "dense", content=oversized), cand(32, 2, "dense", content=remaining)]},
        reranker=FakeReranker(), parent_loader=None, count_tokens=wc,
    )

    [entry] = result.packed.entries
    dropped = next(item for item in result.packed.dropped if item.chunk_id == cid(31))
    selected = {candidate.chunk_id: candidate.selected for candidate in result.ranked}
    context = context_json(result.packed)
    serialized_drop = next(item for item in context["dropped"] if item["chunk_id"] == str(cid(31)))

    assert entry.item.chunk_id == cid(32) and entry.item.text == remaining
    assert dropped.reason == "compression_empty" and dropped.token_count == 201
    assert dropped.dropped_spans == (CharacterSpan(0, len(oversized)),)
    assert serialized_drop["dropped_spans"] == [{"start": 0, "end": len(oversized)}]
    assert selected[cid(31)] is False and selected[cid(32)] is True

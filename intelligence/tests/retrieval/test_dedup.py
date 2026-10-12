from _t14_factories import cand, cid, span
from scribe_intel.retrieval.dedup import dedup
from scribe_intel.retrieval.fusion import rrf_fuse


def R(*cs):
    return rrf_fuse({"dense": list(cs)})


def test_identical_content_in_two_documents_collapses_keeping_provenance():
    a = cand(1, 1, content="same paragraph", doc="a.pdf", source_id=1)
    b = cand(2, 2, content="same paragraph", doc="b.pdf", source_id=2, spans=[span(7, 5, 20)])
    c = cand(3, 3, content="other")
    out = dedup(R(a, b, c))
    assert [d.chunk_id for d in out] == [cid(1), cid(3)]
    first = out[0]
    assert first.duplicates == [cid(2)]
    assert [(p.doc_name, p.source_id, p.spans) for p in first.also_in] == [("b.pdf", 2, tuple(b.spans))]
    assert first.ranked.candidate.spans == a.spans  # own spans untouched


def test_best_ranked_occurrence_wins():
    a = cand(1, 2, content="x", doc="a.pdf")
    b = cand(2, 1, content="x", doc="b.pdf")
    out = dedup(R(a, b))
    assert out[0].chunk_id == cid(2) and out[0].duplicates == [cid(1)]


def test_one_child_per_parent_merges_spans():
    a = cand(1, 1, parent=1, spans=[span(1, 0, 5)])
    b = cand(2, 2, parent=1, spans=[span(2, 0, 5)])
    c = cand(3, 3, parent=2)
    out = dedup(R(a, b, c))
    assert [d.chunk_id for d in out] == [cid(1), cid(3)]
    assert [s.block_index for s in out[0].merged_spans] == [1, 2]
    assert [m.chunk_id for m in out[0].merged_children] == [cid(2)]


def test_parent_merge_can_be_disabled_and_none_parent_never_merges():
    a, b = cand(1, 1, parent=1), cand(2, 2, parent=1)
    assert len(dedup(R(a, b), merge_parents=False)) == 2
    assert len(dedup(R(cand(1, 1), cand(2, 2)))) == 2


def test_hash_dup_before_parent_merge_carries_provenance():
    a = cand(1, 1, parent=1, content="p")
    b = cand(2, 2, parent=1, content="q")
    c = cand(3, 3, parent=1, content="q", doc="z.pdf")  # dup of b, b merged into a
    out = dedup(R(a, b, c))
    assert len(out) == 1
    assert out[0].duplicates == [cid(3)] and out[0].also_in[0].doc_name == "z.pdf"

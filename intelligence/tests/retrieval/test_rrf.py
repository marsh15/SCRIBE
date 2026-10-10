import random

from hypothesis import given, settings, strategies as st

from _t14_factories import cand, cid
from scribe_intel.retrieval.fusion import rrf_fuse, top_n

K = 60


def test_hand_computed_example():
    # dense:   A(1) B(2) C(3)      lexical: B(1) D(2) A(3)
    # A = 1/61 + 1/63 = 0.016393 + 0.015873 = 0.032266
    # B = 1/62 + 1/61 = 0.016129 + 0.016393 = 0.032522
    # C = 1/63 = 0.015873 ; D = 1/62 = 0.016129
    # order: B, A, D, C
    dense = [cand(1, 1), cand(2, 2), cand(3, 3)]  # 1=A 2=B 3=C
    lex = [cand(2, 1, "lexical"), cand(4, 2, "lexical"), cand(1, 3, "lexical")]
    out = rrf_fuse({"dense": dense, "lexical": lex})
    assert [r.chunk_id for r in out] == [cid(2), cid(1), cid(4), cid(3)]
    assert [r.fusion_rank for r in out] == [1, 2, 3, 4]
    assert abs(out[0].fusion_score - (1 / 62 + 1 / 61)) < 1e-12
    b = out[0]
    assert (b.dense_rank, b.lexical_rank) == (2, 1)
    assert out[3].lexical_rank is None and out[3].dense_rank == 3


def test_tie_break_lists_then_best_rank_then_id():
    # X: dense 1 only -> 1/61. Y: lexical 1 only -> 1/61. Equal score, equal lists, equal best rank -> chunk id.
    out = rrf_fuse({"dense": [cand(9, 1)], "lexical": [cand(3, 1, "lexical")]})
    assert [r.chunk_id for r in out] == [cid(3), cid(9)]
    # Z in both at 3 and 3: 2/63=0.03175 beats W at rank 1 only (1/61)
    out = rrf_fuse({"dense": [cand(1, 1), cand(2, 2), cand(5, 3)], "lexical": [cand(6, 1, "lexical"), cand(7, 2, "lexical"), cand(5, 3, "lexical")]})
    assert out[0].chunk_id == cid(5)


def test_k_param():
    out = rrf_fuse({"dense": [cand(1, 1)]}, k=0)
    assert out[0].fusion_score == 1.0


def test_top_n():
    out = rrf_fuse({"dense": [cand(i, i) for i in range(1, 6)]})
    assert [r.fusion_rank for r in top_n(out, 3)] == [1, 2, 3]
    assert top_n(out, 0) == []


def _lists(draw_ids_d, draw_ids_l):
    dense = [cand(n, i + 1, "dense", score=random.random()) for i, n in enumerate(draw_ids_d)]
    lex = [cand(n, i + 1, "lexical", score=random.random()) for i, n in enumerate(draw_ids_l)]
    return {"dense": dense, "lexical": lex}


ids = st.lists(st.integers(0, 30), unique=True, max_size=15)


@given(ids, ids)
@settings(max_examples=200)
def test_ranks_only_scrambling_native_scores_changes_nothing(d, l):
    a = _lists(d, l)
    b = {k: [cand(c.chunk_id.int - 0x1000, c.rank, c.method, score=random.uniform(-1e6, 1e6)) for c in v] for k, v in a.items()}
    ra, rb = rrf_fuse(a), rrf_fuse(b)
    assert [(r.chunk_id, r.fusion_rank, r.fusion_score) for r in ra] == [(r.chunk_id, r.fusion_rank, r.fusion_score) for r in rb]


@given(ids, ids, st.randoms(use_true_random=False))
@settings(max_examples=200)
def test_deterministic_under_input_permutation_with_tied_ranks(d, l, rnd):
    # collapse ranks to create ties inside a list (pairs share a rank), then shuffle input order
    dense = [cand(n, i // 2 + 1, "dense") for i, n in enumerate(d)]
    lex = [cand(n, i // 3 + 1, "lexical") for i, n in enumerate(l)]
    base = rrf_fuse({"dense": dense, "lexical": lex})
    d2, l2 = dense[:], lex[:]
    rnd.shuffle(d2)
    rnd.shuffle(l2)
    again = rrf_fuse({"lexical": l2, "dense": d2})
    assert [(r.chunk_id, r.fusion_rank) for r in base] == [(r.chunk_id, r.fusion_rank) for r in again]


@given(st.integers(1, 200), st.integers(1, 200), st.integers(0, 120))
def test_score_monotone_in_rank(r1, r2, k):
    lo, hi = sorted((r1, r2))
    s_lo = rrf_fuse({"dense": [cand(1, lo)]}, k=k)[0].fusion_score
    s_hi = rrf_fuse({"dense": [cand(1, hi)]}, k=k)[0].fusion_score
    assert s_lo >= s_hi


@given(ids, ids)
@settings(max_examples=300)
def test_both_lists_property(d, l):
    """If X is in both lists and Y only in list L with rank_L(X) <= rank_L(Y), X is fused above Y."""
    lists = _lists(d, l)
    out = rrf_fuse(lists)
    pos = {r.chunk_id: r.fusion_rank for r in out}
    rank = {name: {c.chunk_id: c.rank for c in v} for name, v in lists.items()}
    both = set(rank["dense"]) & set(rank["lexical"])
    for name, other in (("dense", "lexical"), ("lexical", "dense")):
        for y, ry in rank[name].items():
            if y in rank[other]:
                continue
            for x in both:
                if rank[name][x] <= ry:
                    assert pos[x] < pos[y]


def test_duplicate_within_list_uses_best_rank():
    out = rrf_fuse({"dense": [cand(1, 3), cand(1, 1)]})
    assert len(out) == 1 and out[0].dense_rank == 1


def test_empty():
    assert rrf_fuse({}) == [] and rrf_fuse({"dense": [], "lexical": []}) == []

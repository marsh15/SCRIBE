"""Reciprocal rank fusion (DESIGN B4 step 5, ADR-0006, INV-10).

Only RANKS are used. Native scores (cosine, ts_rank, ...) are carried along for the trace but
never enter the fused value, so scrambling them cannot change the order.

    fusion_score(chunk) = sum over lists L containing chunk of 1 / (k + rank_L(chunk))

Rank = `Candidate.rank` (1-based, the retriever's own rank). Candidates that share a rank in
one list (ties) are ordered by str(chunk_id) before being used, so the input order of a list
never matters. If a chunk appears twice in one list only its best rank counts.

Ordering of the fused list: higher score first; ties -> more lists containing the chunk, then
the best single rank, then str(chunk_id). Scores are compared as exact Fractions so float
summation order can never create or break a tie.

BOTH-LISTS PROPERTY (tested): let X appear in lists L1 and L2 and Y appear only in L1. If
rank_L1(X) <= rank_L1(Y) then X is fused strictly above Y, because
score(X) = 1/(k+r1X) + 1/(k+r2X) > 1/(k+r1X) >= 1/(k+r1Y) = score(Y).
In words: being found by a second retriever can never push a chunk below a chunk that
the first retriever alone ranks no better, and a chunk in both lists always beats any
single-list chunk that is not better ranked in that list.
"""

from __future__ import annotations

from fractions import Fraction
from uuid import UUID

from .types import Candidate, RankedCandidate

DEFAULT_K = 60
_NATIVE = {"dense", "lexical"}


def rrf_fuse(lists: dict[str, list[Candidate]], k: int = DEFAULT_K) -> list[RankedCandidate]:
    if k < 0:
        raise ValueError("k must be >= 0")
    # chunk_id -> {list_name: (rank, candidate)}
    seen: dict[UUID, dict[str, tuple[int, Candidate]]] = {}
    for name in sorted(lists):
        for cand in sorted(lists[name], key=lambda c: (c.rank, str(c.chunk_id))):
            if cand.rank < 1:
                raise ValueError(f"rank must be >= 1 (got {cand.rank})")
            slot = seen.setdefault(cand.chunk_id, {})
            if name not in slot:  # best rank wins within one list
                slot[name] = (cand.rank, cand)

    rows: list[tuple[Fraction, int, int, str, RankedCandidate]] = []
    for chunk_id, per in seen.items():
        exact = sum((Fraction(1, k + r) for r, _ in per.values()), Fraction(0))
        best_rank = min(r for r, _ in per.values())
        # representative: best rank, then list name for determinism
        rep_name = min(per, key=lambda n: (per[n][0], n))
        rc = RankedCandidate(chunk_id=chunk_id, candidate=per[rep_name][1])
        for name, (r, c) in per.items():
            if name == "dense":
                rc.dense_rank, rc.dense_score = r, c.score
            elif name == "lexical":
                rc.lexical_rank, rc.lexical_score = r, c.score
        rc.fusion_score = float(exact)
        rows.append((exact, len(per), best_rank, str(chunk_id), rc))

    rows.sort(key=lambda t: (-t[0], -t[1], t[2], t[3]))
    out = []
    for i, (_, _, _, _, rc) in enumerate(rows, start=1):
        rc.fusion_rank = i
        out.append(rc)
    return out


def top_n(ranked: list[RankedCandidate], n: int) -> list[RankedCandidate]:
    """First n by fusion_rank (input order if fusion_rank is unset)."""
    if n < 0:
        raise ValueError("n must be >= 0")
    ordered = sorted(ranked, key=lambda r: (r.fusion_rank is None, r.fusion_rank or 0))
    return ordered[:n]

"""Retrieval metrics for the golden set (DESIGN D17, INV-10/11/12).

Ranking input format (also documented in docs/scribe2/benchmarks/rankings-format.md):
JSONL, one line per case, items ordered best first::

    {"case_id": "sf-01",
     "ranking": [{"chunk_id": "c1", "doc": "a.pdf", "text": "...", "score": 0.83|null, "method": "dense"|null}],
     "latency_ms": 123.4, "cost": {...}|null, "degraded": false|null}

Relevance (D17): a ranked item is relevant to an evidence entry iff
``normalize(item.text)`` contains ``normalize(entry.quote)``. One item can
satisfy several entries; one entry can be satisfied by several items.

Definitions, for an answerable case with n >= 1 evidence entries:
  recall@k  = |entries matched by at least one of the top-k items| / n
  hit@k     = 1 if any of the top-k items matches any entry, else 0
  mrr       = 1 / rank of the first item matching any entry (0 if none)
  ndcg@10   = DCG / IDCG over the top 10. Each entry is credited to the first
              item that matches it; an item's gain is the max grade (1 or 2)
              among entries newly credited to it (0 if none). IDCG places the
              entries' grades in descending order, one per rank. Discount is
              1/log2(rank+1). Value is always in [0, 1].
  precision@5 = (# of top-5 items matching any entry) / 5 (fixed denominator,
              so short rankings are penalised).
Unanswerable cases are excluded from these metrics and reported separately
(count and the top score of the ranking, if it has one).
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .dataset import Case
from .normalize import normalize

METRIC_NAMES: tuple[str, ...] = (
    "recall@1", "recall@5", "recall@10",
    "hit@1", "hit@5", "hit@10",
    "mrr", "ndcg@10", "precision@5",
)
CI_RESAMPLES = 10_000
CI_SEED = 20260101


@dataclass
class RankedItem:
    chunk_id: str | None
    text: str
    doc: str | None = None
    score: float | None = None
    method: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RankedItem":
        return cls(
            chunk_id=d.get("chunk_id"), text=d.get("text") or "", doc=d.get("doc"),
            score=d.get("score"), method=d.get("method"),
        )


def match_matrix(case: Case, ranking: Sequence[RankedItem]) -> list[set[int]]:
    """For each ranked item, the set of evidence indices it satisfies."""
    quotes = [normalize(e.quote) for e in case.evidence]
    out: list[set[int]] = []
    for item in ranking:
        text = normalize(item.text)
        out.append({i for i, q in enumerate(quotes) if q and q in text})
    return out


def _ndcg(case: Case, matches: list[set[int]], k: int = 10) -> float:
    grades = [e.grade for e in case.evidence]
    credited: set[int] = set()
    dcg = 0.0
    for pos, m in enumerate(matches[:k], 1):
        new = m - credited
        if new:
            dcg += max(grades[i] for i in new) / math.log2(pos + 1)
            credited |= new
    ideal = sorted(grades, reverse=True)[:k]
    idcg = sum(g / math.log2(p + 1) for p, g in enumerate(ideal, 1))
    return dcg / idcg if idcg else 0.0


def case_metrics(case: Case, ranking: Sequence[RankedItem]) -> dict[str, float]:
    """Metrics for one answerable case with evidence."""
    if not case.answerable or not case.evidence:
        raise ValueError(f"case {case.id} has no evidence; exclude it from retrieval metrics")
    matches = match_matrix(case, ranking)
    n = len(case.evidence)
    m: dict[str, float] = {}
    for k in (1, 5, 10):
        found = set().union(*matches[:k]) if matches[:k] else set()
        m[f"recall@{k}"] = len(found) / n
        m[f"hit@{k}"] = 1.0 if found else 0.0
    m["mrr"] = next((1.0 / r for r, s in enumerate(matches, 1) if s), 0.0)
    m["ndcg@10"] = _ndcg(case, matches, 10)
    m["precision@5"] = sum(1 for s in matches[:5] if s) / 5
    return m


def top_score(ranking: Sequence[RankedItem]) -> float | None:
    """Score of the first item that has one (false-positive tracking for unanswerable cases)."""
    for item in ranking:
        if item.score is not None:
            return float(item.score)
    return None


def bootstrap_ci_mean(
    values: Sequence[float], n_resamples: int = CI_RESAMPLES, seed: int = CI_SEED, alpha: float = 0.05
) -> tuple[float, float]:
    """Percentile bootstrap CI on the mean (pure python, fixed seed)."""
    n = len(values)
    if n == 0:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_resamples))
    lo = means[int((alpha / 2) * n_resamples)]
    hi = means[min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))]
    return (lo, hi)


@dataclass
class CaseResult:
    case_id: str
    category: str
    answerable: bool
    metrics: dict[str, float] | None
    ranking: list[dict[str, Any]]
    latency_ms: float | None = None
    cost: dict[str, Any] | None = None
    degraded: bool | None = None
    top_score: float | None = None


def evaluate_case(case: Case, record: dict[str, Any]) -> CaseResult:
    items = [RankedItem.from_dict(d) for d in record.get("ranking", [])]
    answerable = case.answerable and bool(case.evidence)
    return CaseResult(
        case_id=case.id, category=case.category, answerable=answerable,
        metrics=case_metrics(case, items) if answerable else None,
        ranking=[d for d in record.get("ranking", [])],
        latency_ms=record.get("latency_ms"), cost=record.get("cost"), degraded=record.get("degraded"),
        top_score=None if answerable else top_score(items),
    )


@dataclass
class Aggregate:
    n: int
    mean: dict[str, float] = field(default_factory=dict)
    ci95: dict[str, tuple[float, float]] = field(default_factory=dict)


def aggregate(results: Iterable[CaseResult], with_ci: bool = True) -> Aggregate:
    rows = [r.metrics for r in results if r.metrics is not None]
    agg = Aggregate(n=len(rows))
    for name in METRIC_NAMES:
        vals = [r[name] for r in rows]
        if not vals:
            continue
        agg.mean[name] = statistics.fmean(vals)
        if with_ci:
            agg.ci95[name] = bootstrap_ci_mean(vals)
    return agg


def summarize(results: Sequence[CaseResult], with_ci: bool = True) -> dict[str, Any]:
    """Overall + per-category aggregates and the unanswerable report."""
    by_cat: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        if r.answerable:
            by_cat[r.category].append(r)
    unans = [r for r in results if not r.answerable]
    scores = [r.top_score for r in unans if r.top_score is not None]
    return {
        "overall": aggregate([r for r in results if r.answerable], with_ci),
        "per_category": {c: aggregate(rs, with_ci) for c, rs in sorted(by_cat.items())},
        "unanswerable": {
            "count": len(unans),
            "top_scores": {r.case_id: r.top_score for r in unans},
            "max_top_score": max(scores) if scores else None,
            "mean_top_score": statistics.fmean(scores) if scores else None,
        },
    }

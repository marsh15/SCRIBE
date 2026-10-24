"""Retrieval metrics for the golden set (DESIGN D17, INV-10/11/12).

Ranking input format (also documented in docs/scribe2/benchmarks/rankings-format.md):
JSONL, one line per case, items ordered best first::

    {"case_id": "sf-01",
     "ranking": [{"chunk_id": "c1", "doc": "a.pdf", "text": "...", "score": 0.83|null, "method": "dense"|null}],
     "latency_ms": 123.4, "cost": {...}|null, "degraded": false|null}

Relevance (D17): a ranked item is relevant to an evidence entry iff
``normalize(item.text)`` contains ``normalize(entry.quote)``. One item can
satisfy several entries; one entry can be satisfied by several items.

CSV exception: CSV evidence quotes are raw record lines (``a,b,c``), but each
pipeline renders rows differently (legacy joins cells with spaces; Scribe 2.0
renders ``col: val``). For a quote whose document is a ``.csv`` the entry is
also satisfied when every non-empty cell of the record, normalized, appears in
order in the item text. This keeps labels independent of any profile.

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

A4a additions (additive: no existing key is renamed or removed)
---------------------------------------------------------------
Rankings rows written by `run-profile` also carry provenance per item: ``workspace_id``, ``source_id`` and
``source_deleted``; and per record ``embed_ms``, ``retrieval_ms`` and ``embed_cached`` next to ``latency_ms`` (which is
now embed + retrieval, the user-facing latency). Records carry ``meta.workspace_id`` (the dataset's eval workspace).

  leak               per case (all cases, answerable or not): leaked iff ANY of the top ``LEAK_DEPTH`` (10) ranked items
                     or any cited item is (a) from a ``forbidden`` doc of the case, (b) from a workspace other than the
                     dataset's eval workspace (a decoy workspace), or (c) from a soft-deleted source.
                     ``leak_rate`` = leaked cases / cases. It must be 0 for a release (gate comes later; only reported
                     here). ``leak_rate_verified`` is False when some item lacked provenance (old rankings files): the
                     decoy/soft-delete checks could not run for those items, so 0.0 then proves nothing.
  citation validity  deterministic, per answer: a citation is valid iff its label resolves to an item of the turn's
                     bundle, sha256(quote) equals the recorded quote hash, and the quote lies within that item's text.
                     Hashing reuses ``scribe_intel.verification.sha256_hex``; labels use its ``claim_labels``. The DB
                     bound ``check_item`` is NOT reused: it needs document_blocks rows (and requires quote == whole
                     chunk), which an offline bundle does not have.
  latency            p50 / p95 by the nearest-rank method over per-case ``latency_ms`` (total), ``embed_ms`` and
                     ``retrieval_ms``. ``embed_live_n`` counts cases whose query embedding was really requested from
                     the provider; a cached embedding makes ``embed_ms`` ~0, so the total is only a real latency when
                     ``embed_live_n == n``.
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
LEAK_DEPTH = 10
LEAK_FORBIDDEN, LEAK_DECOY, LEAK_DELETED = "forbidden_doc", "decoy_workspace", "soft_deleted_source"
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


def csv_cells(raw_line: str) -> list[str]:
    """Normalized non-empty cells of a raw CSV record (delimiter guessed)."""
    import csv

    best: list[str] = []
    for delim in (",", ";", "\t", "|"):
        try:
            row = next(csv.reader([raw_line], delimiter=delim))
        except (csv.Error, StopIteration):
            continue
        if len(row) > len(best):
            best = row
    return [c for c in (normalize(c) for c in best) if c]


def _cells_in_order(cells: list[str], text: str) -> bool:
    pos = 0
    for cell in cells:
        found = text.find(cell, pos)
        if found < 0:
            return False
        pos = found + len(cell)
    return bool(cells)


def match_matrix(case: Case, ranking: Sequence[RankedItem]) -> list[set[int]]:
    """For each ranked item, the set of evidence indices it satisfies."""
    quotes = [normalize(e.quote) for e in case.evidence]
    csv_entries = {
        i: csv_cells(e.quote)
        for i, e in enumerate(case.evidence)
        if e.doc.lower().endswith(".csv")
    }
    out: list[set[int]] = []
    for item in ranking:
        text = normalize(item.text)
        hit = {i for i, q in enumerate(quotes) if q and q in text}
        for i, cells in csv_entries.items():
            if i not in hit and _cells_in_order(cells, text):
                hit.add(i)
        out.append(hit)
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
    leak: dict[str, Any] | None = None
    timing: dict[str, Any] | None = None  # embed_ms / retrieval_ms / embed_cached from the rankings record


def item_leak_reasons(forbidden_docs: set[str], item: dict[str, Any], expected_workspace_id: str | None) -> list[str]:
    reasons = []
    if item.get("doc") in forbidden_docs:
        reasons.append(LEAK_FORBIDDEN)
    ws = item.get("workspace_id")
    if expected_workspace_id is not None and ws is not None and str(ws) != str(expected_workspace_id):
        reasons.append(LEAK_DECOY)
    if item.get("source_deleted") is True:
        reasons.append(LEAK_DELETED)
    return reasons


def case_leak(case: Case, ranking: Sequence[dict[str, Any]], expected_workspace_id: str | None = None,
              cited: Sequence[dict[str, Any]] | None = None, depth: int = LEAK_DEPTH) -> dict[str, Any]:
    """Leak verdict for one case: top-``depth`` ranked items plus every cited item (see module docstring)."""
    forbidden = {f.doc for f in case.forbidden}
    hits: list[dict[str, Any]] = []
    unknown = 0
    seen = [("rank", i, it) for i, it in enumerate(list(ranking)[:depth], 1)] + \
           [("cited", i, it) for i, it in enumerate(cited or [], 1)]
    for where, pos, it in seen:
        if (expected_workspace_id is None or it.get("workspace_id") is None or
                it.get("source_deleted") is None):
            unknown += 1
        reasons = item_leak_reasons(forbidden, it, expected_workspace_id)
        if reasons:
            hits.append({"where": where, "position": pos, "chunk_id": it.get("chunk_id"), "doc": it.get("doc"),
                         "reasons": reasons})
    return {"leaked": bool(hits), "reasons": sorted({r for h in hits for r in h["reasons"]}), "items": hits,
            "provenance_missing_items": unknown}


def leak_summary(leaks: Iterable[tuple[str, dict[str, Any] | None]]) -> dict[str, Any]:
    rows = [(cid, lk) for cid, lk in leaks if lk is not None]
    leaked = [cid for cid, lk in rows if lk["leaked"]]
    missing = sum(lk.get("provenance_missing_items", 0) for _, lk in rows)
    return {"cases": len(rows), "leaked_cases": len(leaked), "leaked_case_ids": leaked,
            "leak_rate": (len(leaked) / len(rows)) if rows else None,
            "leak_rate_verified": bool(rows) and missing == 0, "provenance_missing_items": missing}


# ---- deterministic citation validity (reuses scribe_intel.verification helpers)

@dataclass(frozen=True)
class Citation:
    label: str
    quote: str
    quote_hash: str


def check_citation(citation: Citation, bundle: dict[str, str]) -> str | None:
    """Failure code, or None when valid. ``bundle`` maps label -> chunk text. Codes follow verification.check_item:
    label_not_in_turn, quote_hash_mismatch, plus quote_not_in_chunk (the span lies outside the chunk text)."""
    from scribe_intel.verification import sha256_hex

    chunk = bundle.get(citation.label)
    if chunk is None:
        return "label_not_in_turn"
    if sha256_hex(citation.quote) != citation.quote_hash:
        return "quote_hash_mismatch"
    if not citation.quote or citation.quote not in chunk:
        return "quote_not_in_chunk"
    return None


def citation_validity(citations: Sequence[Citation], bundle: dict[str, str]) -> dict[str, Any]:
    failures = []
    for c in citations:
        code = check_citation(c, bundle)
        if code:
            failures.append({"label": c.label, "code": code})
    n = len(citations)
    return {"total": n, "valid": n - len(failures), "failures": failures,
            "rate": ((n - len(failures)) / n) if n else None}


# ---- latency

def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in (0, 1]): the value at rank ceil(q * n) of the sorted values."""
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(q * len(ordered)) - 1)])


def latency_summary(records: Iterable[dict[str, Any] | None]) -> dict[str, Any]:
    """p50/p95 of total (``latency_ms``), ``embed_ms`` and ``retrieval_ms`` over rankings records / result rows."""
    rows = [r for r in records if r]
    out: dict[str, Any] = {"n": len(rows)}
    for key, name in (("latency_ms", "total"), ("embed_ms", "embed"), ("retrieval_ms", "retrieval")):
        vals = [float(r[key]) for r in rows if r.get(key) is not None]
        out[f"{name}_p50_ms"] = percentile(vals, 0.50)
        out[f"{name}_p95_ms"] = percentile(vals, 0.95)
    timed = [r for r in rows if r.get("embed_cached") is not None]
    out["embed_live_n"] = sum(1 for r in timed if r["embed_cached"] is False)
    out["embed_timed_n"] = len(timed)
    return out


def evaluate_case(case: Case, record: dict[str, Any], expected_workspace_id: str | None = None) -> CaseResult:
    items = [RankedItem.from_dict(d) for d in record.get("ranking", [])]
    answerable = case.answerable and bool(case.evidence)
    ws = expected_workspace_id or (record.get("meta") or {}).get("workspace_id")
    return CaseResult(
        case_id=case.id, category=case.category, answerable=answerable,
        metrics=case_metrics(case, items) if answerable else None,
        ranking=[d for d in record.get("ranking", [])],
        latency_ms=record.get("latency_ms"), cost=record.get("cost"), degraded=record.get("degraded"),
        top_score=None if answerable else top_score(items),
        leak=case_leak(case, record.get("ranking", []), ws),
        timing={k: record.get(k) for k in ("embed_ms", "retrieval_ms", "embed_cached") if k in record} or None,
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
    latency_rows = [{"latency_ms": r.latency_ms, **(r.timing or {})} for r in results]
    return {
        "leak": leak_summary((r.case_id, r.leak) for r in results),
        "latency": latency_summary(latency_rows),
        "overall": aggregate([r for r in results if r.answerable], with_ci),
        "per_category": {c: aggregate(rs, with_ci) for c, rs in sorted(by_cat.items())},
        "unanswerable": {
            "count": len(unans),
            "top_scores": {r.case_id: r.top_score for r in unans},
            "max_top_score": max(scores) if scores else None,
            "mean_top_score": statistics.fmean(scores) if scores else None,
        },
    }

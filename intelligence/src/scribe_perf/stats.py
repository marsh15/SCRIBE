"""Pure helpers: latency percentiles and recall."""

from __future__ import annotations

import math
from typing import Hashable, Sequence


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (p in (0, 100]). Deterministic, no interpolation."""
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0 < p <= 100:
        raise ValueError("p must be in (0, 100]")
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def latency_summary(ms: Sequence[float]) -> dict[str, float]:
    if not ms:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "max": 0.0}
    return {"n": len(ms), "p50": round(percentile(ms, 50), 3), "p95": round(percentile(ms, 95), 3),
            "p99": round(percentile(ms, 99), 3), "mean": round(sum(ms) / len(ms), 3), "max": round(max(ms), 3)}


def recall_at_k(truth: Sequence[Hashable], got: Sequence[Hashable], k: int) -> float:
    """|top-k(truth) intersect top-k(got)| / |top-k(truth)|. 1.0 when truth is empty (nothing to find)."""
    t = list(truth)[:k]
    if not t:
        return 1.0
    return len(set(t) & set(list(got)[:k])) / len(t)


def mean_recall(pairs: Sequence[tuple[Sequence[Hashable], Sequence[Hashable]]], k: int) -> float:
    if not pairs:
        return 1.0
    return sum(recall_at_k(t, g, k) for t, g in pairs) / len(pairs)


def throughput(total_queries: int, wall_s: float) -> float:
    return total_queries / wall_s if wall_s > 0 else 0.0

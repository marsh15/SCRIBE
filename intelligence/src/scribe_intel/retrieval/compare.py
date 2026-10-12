"""Pure logic of POST /compare (T29): rank movement and alignment of the rankings of 2-5 profiles.

No DB, no network. Ranks are 1-based positions inside each profile's own returned top-N list (N = request limit);
``None`` means "not in that profile's top N", which is NOT the same as "not retrieved at all". Scores are never
combined across rankers (INV-10): the alignment carries ranks only.
"""

from __future__ import annotations

from typing import Any, Literal, Sequence

Direction = Literal["same", "up", "down", "new", "dropped", "absent"]

MIN_PROFILES = 2
MAX_PROFILES = 5
SNIPPET_CHARS = 320


def rank_movement(baseline: int | None, rank: int | None) -> dict[str, Any]:
    """Movement of a chunk's rank relative to the baseline profile.

    ``delta = baseline - rank`` so a positive delta means the chunk moved UP (towards rank 1). ``places`` is |delta|.
    ``new``: absent from the baseline top N but present here; ``dropped``: the reverse; ``absent``: in neither."""
    if baseline is None and rank is None:
        return {"direction": "absent", "delta": None, "places": None}
    if baseline is None:
        return {"direction": "new", "delta": None, "places": None}
    if rank is None:
        return {"direction": "dropped", "delta": None, "places": None}
    delta = baseline - rank
    direction: Direction = "same" if delta == 0 else ("up" if delta > 0 else "down")
    return {"direction": direction, "delta": delta, "places": abs(delta)}


def snippet(content: str, limit: int = SNIPPET_CHARS) -> str:
    text = " ".join(content.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_alignment(order: Sequence[str], lists: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """`order[0]` is the baseline. `lists[profile]` is that profile's results in rank order, each a dict with at least
    ``rank`` and ``chunk_id`` (plus display fields doc_name, heading_text, snippet, source_id).

    Returns ``{"baseline", "rows", "summary"}``. Rows follow the baseline order first (so the baseline column is
    monotonic and every other column visibly moves), then the chunks the baseline did not return, by best rank."""
    baseline = order[0]
    info: dict[str, dict[str, Any]] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name in order:
        for r in lists.get(name, []):
            cid = r["chunk_id"]
            info.setdefault(cid, {k: r.get(k) for k in ("doc_name", "heading_text", "snippet", "source_id")})
            ranks.setdefault(cid, {})[name] = r["rank"]

    rows: list[dict[str, Any]] = []
    for cid, per in ranks.items():
        base_rank = per.get(baseline)
        present = sorted(per.values())
        rows.append({
            "chunk_id": cid, **info[cid],
            "ranks": {n: per.get(n) for n in order},
            "movement": {n: rank_movement(base_rank, per.get(n)) for n in order},
            "present_in": len(per), "best_rank": present[0], "worst_rank": present[-1],
            "rank_spread": present[-1] - present[0],
        })
    rows.sort(key=lambda r: (r["ranks"][baseline] is None, r["ranks"][baseline] or 0, r["best_rank"], r["chunk_id"]))

    base_ids = set(ranks_ids(ranks, baseline))
    summary: dict[str, dict[str, Any]] = {}
    for n in order:
        ids = set(ranks_ids(ranks, n))
        counts = {"same": 0, "up": 0, "down": 0, "new": 0, "dropped": 0}
        for row in rows:
            d = row["movement"][n]["direction"]
            if d in counts:
                counts[d] += 1
        union = base_ids | ids
        summary[n] = {**counts, "returned": len(ids), "overlap_with_baseline": len(base_ids & ids),
                      "jaccard_with_baseline": round(len(base_ids & ids) / len(union), 4) if union else 1.0}
    return {"baseline": baseline, "rows": rows, "summary": summary}


def ranks_ids(ranks: dict[str, dict[str, int]], profile: str) -> list[str]:
    return [cid for cid, per in ranks.items() if profile in per]

"""Crossover and markdown rendering from a result document (pure functions, unit-tested)."""

from __future__ import annotations


def _forced_default(entry: dict) -> dict | None:
    for g in entry.get("hnsw_forced", {}).get("grid", []):
        if g["ef_search"] == 100 and g["iterative_scan"] == "relaxed_order":
            return g
    return None


def crossover_points(doc: dict) -> list[dict]:
    """One row per (scale, workspace): n_chunks in that workspace; exact vs ANN-as-planned vs HNSW-forced (ef 100, relaxed)."""
    rows = []
    for sc in doc["scales"]:
        for ws, e in sorted(sc.get("dense", {}).items()):
            p, f = e.get("ann_planner"), _forced_default(e)
            if p is None or f is None:
                continue
            rows.append({"scale": sc["scale"], "workspace": ws, "n_chunks": e["n_chunks"],
                         "exact_p50": e["exact_vector"]["latency_ms"]["p50"], "exact_p95": e["exact_vector"]["latency_ms"]["p95"],
                         "ann_p50": p["latency_ms"]["p50"], "ann_p95": p["latency_ms"]["p95"], "ann_recall10": p["recall10"],
                         "ann_uses_hnsw": p["uses_hnsw"], "ann_mean_rows": p["mean_rows"],
                         "forced_p50": f["latency_ms"]["p50"], "forced_p95": f["latency_ms"]["p95"],
                         "forced_recall10": f["recall10"], "eligible_p50": e["eligible_ids_ms"]["p50"]})
    return sorted(rows, key=lambda r: r["n_chunks"])


def first_ann_faster(points: list[dict]) -> int | None:
    """Smallest measured workspace size at which HNSW-forced p50 is lower than exact p50 (None if never)."""
    for p in points:
        if p["forced_p50"] < p["exact_p50"]:
            return p["n_chunks"]
    return None


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render_markdown(doc: dict) -> str:
    parts: list[str] = []
    for sc in doc["scales"]:
        parts.append(f"### Scale {sc['scale']} chunks")
        if "skipped" in sc:
            parts.append(f"SKIPPED: {sc['skipped']}\n")
            continue
        st = sc["storage"]
        b = st["bytes_per_embedding"]
        parts.append(f"Load+index {sc['load_and_index_s']} s; build s: {sc['build_s']}; "
                     f"free disk GB before/after load/after measure: {sc['disk_free_gb_before']}/"
                     f"{sc['disk_free_gb_after_load']}/{sc['disk_free_gb_after_measure']}\n")
        parts.append(_table(["metric", "vector(1536)", "halfvec(1536)"], [
            ["avg embedding column bytes", st["avg_column_bytes_vector"], st["avg_column_bytes_halfvec"]],
            ["table heap bytes / embedding", b["table_vector"], b["table_halfvec"]],
            ["HNSW bytes / embedding", b["hnsw_vector"], b["hnsw_halfvec"]],
            ["HNSW index total bytes", st["relation_bytes"]["hnsw_vector"], st["relation_bytes"]["hnsw_halfvec"]]]))
        parts.append("")
        for ws, e in sorted(sc["dense"].items()):
            parts.append(f"**{ws} ({e['n_chunks']} chunks, {e['n_queries']} queries; eligible-id array build p50 "
                         f"{e['eligible_ids_ms']['p50']} ms)**\n")
            rows = [["exact vector (seq scan)", "-", "-", *_lat(e["exact_vector"]["latency_ms"]), "1.0000", "1.0000", "-"],
                    ["exact halfvec", "-", "-", *_lat(e["exact_halfvec"]["latency_ms"]), e["exact_halfvec"]["recall10"],
                     e["exact_halfvec"]["recall40"], "-"]]
            p = e.get("ann_planner")
            if p:
                rows.append(["ann as planned (vector)", 100, "relaxed_order", *_lat(p["latency_ms"]), p["recall10"], p["recall40"],
                             f"hnsw={p['uses_hnsw']}, rows={p['mean_rows']}"])
            hf = e.get("hnsw_forced", {})
            for g in hf.get("grid", []):
                rows.append(["HNSW forced (vector)", g["ef_search"], g["iterative_scan"], *_lat(g["latency_ms"]), g["recall10"],
                             g["recall40"], f"rows={g['mean_rows']}"])
            h = hf.get("halfvec_ef100_relaxed")
            if h:
                rows.append(["HNSW forced (halfvec)", 100, "relaxed_order", *_lat(h["latency_ms"]), h["recall10"], h["recall40"],
                             f"rows={h['mean_rows']}"])
            parts.append(_table(["path", "ef_search", "iterative_scan", "p50 ms", "p95 ms", "p99 ms", "recall@10", "recall@40",
                                 "notes"], rows))
            parts.append("")
        parts.append(_table(["workspace", "chunks", "p50 ms", "p95 ms", "p99 ms", "mean hits"],
                            [[w, e["n_chunks"], *_lat(e["latency_ms"]), e["mean_hits"]] for w, e in sorted(sc["lexical"].items())]))
        parts.append("")
        for mode, rows in sc["concurrency"].items():
            parts.append(f"Concurrency, requested mode `{mode}`:\n")
            parts.append(_table(["clients", "queries", "qps", "p50 ms", "p95 ms", "p99 ms", "mode used"],
                                [[r["clients"], r["queries"], r["qps"], *_lat(r["latency_ms"]), ",".join(r["mode_used"])] for r in rows]))
            parts.append("")
    pts = crossover_points(doc)
    parts.append("### Crossover (ef_search=100, relaxed_order; workspace chunks = rows eligible for the query)")
    parts.append(_table(["workspace chunks", "scale", "ws", "exact p50", "exact p95", "ann-as-planned p50", "ann uses HNSW",
                         "ann recall@10", "HNSW-forced p50", "HNSW-forced p95", "HNSW-forced recall@10", "eligible-array p50"],
                        [[p["n_chunks"], p["scale"], p["workspace"], p["exact_p50"], p["exact_p95"], p["ann_p50"],
                          p["ann_uses_hnsw"], p["ann_recall10"], p["forced_p50"], p["forced_p95"], p["forced_recall10"],
                          p["eligible_p50"]] for p in pts]))
    return "\n".join(parts)


def _lat(l: dict) -> list:
    return [l["p50"], l["p95"], l["p99"]]

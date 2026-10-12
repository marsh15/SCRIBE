"""T32: pure helpers (no DB)."""
import json
import random

import pytest
from scribe_perf.analysis import crossover_points, first_ann_faster, render_markdown
from scribe_perf.load import plan_workspaces
from scribe_perf.pgdb import admin_url, require_throwaway, with_database
from scribe_perf.runner import cycle_to, required_free_gb
from scribe_perf.stats import latency_summary, mean_recall, percentile, recall_at_k, throughput
from scribe_perf.synth import DEFAULT_FRACTIONS, gauss_unit, make_vocab, perturb, split_counts


def test_percentile_nearest_rank():
    xs = list(range(1, 101))
    assert percentile(xs, 50) == 50 and percentile(xs, 95) == 95 and percentile(xs, 99) == 99 and percentile(xs, 100) == 100
    assert percentile([5.0], 99) == 5.0
    assert percentile([3, 1, 2], 50) == 2  # unsorted input
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1], 0)


def test_latency_summary():
    s = latency_summary([1.0, 2.0, 3.0, 4.0])
    assert s == {"n": 4, "p50": 2.0, "p95": 4.0, "p99": 4.0, "mean": 2.5, "max": 4.0}
    assert latency_summary([])["n"] == 0


def test_recall_at_k():
    assert recall_at_k([1, 2, 3, 4], [1, 2, 9, 8], 4) == 0.5
    assert recall_at_k([1, 2, 3, 4], [4, 3, 2, 1], 4) == 1.0  # order-insensitive
    assert recall_at_k([1, 2, 3, 4], [9, 9, 1, 2], 2) == 0.0  # only the top-k of each list counts
    assert recall_at_k([], [1], 10) == 1.0
    assert recall_at_k([1, 2], [1], 10) == 0.5  # a shortfall (fewer rows than truth) lowers recall
    assert mean_recall([([1, 2], [1, 2]), ([1, 2], [3, 4])], 2) == 0.5
    assert mean_recall([], 10) == 1.0


def test_throughput():
    assert throughput(100, 2.0) == 50.0 and throughput(5, 0) == 0.0


def test_split_counts_sums_and_is_positive():
    for total in (10, 200, 1000, 10_000, 100_000):
        c = split_counts(total)
        assert sum(c) == total and all(x >= 1 for x in c) and len(c) == len(DEFAULT_FRACTIONS)
    assert split_counts(1000)[0] == 700


def test_vectors_are_unit_and_deterministic():
    a = gauss_unit(random.Random(1), 64)
    assert abs(sum(x * x for x in a) - 1) < 1e-9
    assert a == gauss_unit(random.Random(1), 64)
    p = perturb(a, 0.5, random.Random(2))
    assert abs(sum(x * x for x in p) - 1) < 1e-9 and sum(x * y for x, y in zip(a, p)) > 0.5


def test_vocab_is_seeded_and_unique():
    v = make_vocab(32, 300)
    assert v == make_vocab(32, 300) and len(set(v)) == 300 and v != make_vocab(33, 300)


def test_plan_is_deterministic_and_exact():
    p1, p2 = plan_workspaces(200, 7), plan_workspaces(200, 7)
    assert [w.n_children for w in p1] == [140, 40, 14, 4, 2]
    assert [str(w.id) for w in p1] == [str(w.id) for w in p2]
    for w in p1:
        assert sum(len(d.child_ids) for d in w.docs) == w.n_children
    assert len({str(c) for w in p1 for d in w.docs for c in d.child_ids}) == 200


def test_throwaway_guard_and_urls():
    assert require_throwaway("postgresql://u:p@h:1/perf_x") == "perf_x"
    for bad in ("scribe", "t13_test", "postgres", "perf"):
        with pytest.raises(ValueError):
            require_throwaway(f"postgresql://u:p@h:1/{bad}")
    assert admin_url("postgresql://u:p@h:1/perf_x") == "postgresql://u:p@h:1/postgres"
    assert with_database("postgresql://u:p@h:1/a", "perf_b") == "postgresql://u:p@h:1/perf_b"


def test_disk_gate_thresholds():
    assert required_free_gb(100_000) == 8.0 and required_free_gb(1_000_000) == 8.0 and required_free_gb(10_000) < 8.0
    assert cycle_to([1, 2, 3], 7) == [1, 2, 3, 1, 2, 3, 1] and cycle_to([], 5) == []


def _lat(p50):
    return {"p50": p50, "p95": p50 * 2, "p99": p50 * 3}


def _doc():
    def ws(n, exact, ann):
        return {"n_chunks": n, "n_queries": 5, "exact_vector": {"latency_ms": _lat(exact), "uses_hnsw": False},
                "exact_halfvec": {"latency_ms": _lat(exact), "recall10": 1.0, "recall40": 1.0, "mean_rows": 40},
                "eligible_ids_ms": {"p50": 1.0},
                "ann_planner": {"latency_ms": _lat(exact), "recall10": 1.0, "recall40": 1.0, "mean_rows": 40, "uses_hnsw": False},
                "hnsw_forced": {"uses_hnsw": True, "grid": [
                    {"ef_search": 100, "iterative_scan": "relaxed_order", "latency_ms": _lat(ann), "recall10": 0.97,
                     "recall40": 0.9, "mean_rows": 40}]}}
    return {"scales": [
        {"scale": 1000, "dense": {"ws0": ws(700, 5.0, 7.0)}},
        {"scale": 10000, "dense": {"ws0": ws(7000, 20.0, 9.0)}},
        {"scale": 5, "skipped": "free disk 1 GB < required 8 GB"}]}


def test_crossover_points_and_first_faster():
    pts = crossover_points(_doc())
    assert [p["n_chunks"] for p in pts] == [700, 7000]
    assert first_ann_faster(pts) == 7000  # HNSW-forced p50 < exact p50 first holds at 7000
    assert first_ann_faster(pts[:1]) is None


def test_render_markdown_reports_skips_and_crossover():
    d = _doc()
    d["scales"] = [d["scales"][2]]
    md = render_markdown(d)
    assert "SKIPPED" in md and "Crossover" in md
    json.dumps(crossover_points(_doc()))

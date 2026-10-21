"""A4a (offline, no DB): recall@10, leak_rate, deterministic citation validity, latency percentiles, cost math and the
spend ledger. Synthetic rankings only; nothing touches a provider."""
from __future__ import annotations

import hashlib
import json

import pytest

from scribe_eval import cost
from scribe_eval.dataset import Case
from scribe_eval.metrics import (
    METRIC_NAMES, Citation, case_leak, case_metrics, check_citation, citation_validity, evaluate_case,
    latency_summary, leak_summary, percentile, summarize, RankedItem,
)

WS, DECOY = "w-eval", "w-decoy"


def make_case(cid="c1", evidence=("alpha fact", "beta fact"), forbidden=("old.pdf",), **over) -> Case:
    d = {"id": cid, "split": "dev", "category": "single-fact", "question": "q?", "answerable": True,
         "reference_answer": "r", "reviewed": True,
         "evidence": [{"doc": "a.md", "version": 1, "quote": q, "grade": 1} for q in evidence],
         "forbidden": [{"doc": f} for f in forbidden]}
    d.update(over)
    return Case.model_validate(d)


def item(doc="a.md", text="filler", ws=WS, deleted=False, cid="k"):
    return {"chunk_id": cid, "doc": doc, "text": text, "workspace_id": ws, "source_deleted": deleted}


# ---------------------------------------------------------------- recall@10

def test_recall_at_10_is_a_metric_name_and_counts_rank_ten_not_eleven():
    assert "recall@10" in METRIC_NAMES
    case = make_case()
    filler = [RankedItem(None, f"noise {i}") for i in range(12)]

    def ranked(pos_a, pos_b):
        items = list(filler)
        items[pos_a - 1] = RankedItem(None, "the alpha fact is here")
        items[pos_b - 1] = RankedItem(None, "the beta fact is here")
        return items

    m = case_metrics(case, ranked(3, 10))
    assert m["recall@5"] == 0.5 and m["recall@10"] == 1.0
    m = case_metrics(case, ranked(3, 11))
    assert m["recall@10"] == 0.5
    assert summarize([evaluate_case(case, {"ranking": [item(text="alpha fact beta fact")]})])["overall"].mean["recall@10"] == 1.0


# ---------------------------------------------------------------- leak_rate

def test_clean_ranking_has_zero_leak():
    lk = case_leak(make_case(), [item(cid=f"k{i}") for i in range(10)], WS)
    assert lk["leaked"] is False and lk["reasons"] == [] and lk["provenance_missing_items"] == 0
    assert leak_summary([("c1", lk)])["leak_rate"] == 0.0 and leak_summary([("c1", lk)])["leak_rate_verified"] is True


def test_forbidden_doc_decoy_workspace_and_soft_deleted_each_leak():
    case = make_case()
    forb = case_leak(case, [item(), item(doc="old.pdf")], WS)
    assert forb["leaked"] and forb["reasons"] == ["forbidden_doc"] and forb["items"][0]["position"] == 2
    decoy = case_leak(case, [item(), item(ws=DECOY)], WS)
    assert decoy["leaked"] and decoy["reasons"] == ["decoy_workspace"]
    dele = case_leak(case, [item(deleted=True)], WS)
    assert dele["leaked"] and dele["reasons"] == ["soft_deleted_source"]


def test_leak_only_looks_at_top_ten_but_always_at_cited_items():
    case = make_case()
    ranking = [item(cid=f"k{i}") for i in range(10)] + [item(doc="old.pdf")]  # forbidden at rank 11
    assert case_leak(case, ranking, WS)["leaked"] is False
    assert case_leak(case, ranking, WS, cited=[item(ws=DECOY)])["reasons"] == ["decoy_workspace"]


def test_leak_rate_is_leaked_cases_over_cases_and_flags_missing_provenance():
    case = make_case()
    results = [("a", case_leak(case, [item()], WS)), ("b", case_leak(case, [item(ws=DECOY)], WS)),
               ("c", case_leak(case, [item()], WS)), ("d", case_leak(case, [item(doc="old.pdf")], WS))]
    s = leak_summary(results)
    assert s["leak_rate"] == 0.5 and s["leaked_case_ids"] == ["b", "d"] and s["leak_rate_verified"] is True
    old_format = {"chunk_id": "x", "doc": "a.md", "text": "t"}  # a pre-A4a rankings row
    unverified = leak_summary([("a", case_leak(case, [old_format], WS))])
    assert unverified["leak_rate"] == 0.0 and unverified["leak_rate_verified"] is False


def test_evaluate_case_uses_workspace_from_record_meta_and_summary_carries_leak():
    case = make_case()
    rec = {"case_id": "c1", "ranking": [item(text="alpha fact beta fact"), item(ws=DECOY)], "meta": {"workspace_id": WS}}
    res = evaluate_case(case, rec)
    assert res.leak["leaked"] and res.leak["reasons"] == ["decoy_workspace"]
    s = summarize([res])
    assert s["leak"]["leak_rate"] == 1.0 and "overall" in s and "latency" in s  # existing keys untouched
    unanswerable = make_case("u1", evidence=(), answerable=False)
    assert evaluate_case(unanswerable, {"ranking": [item(doc="old.pdf")], "meta": {"workspace_id": WS}}).leak["leaked"]


# ---------------------------------------------------------------- citation validity

def _cit(label="E1", quote="alpha fact", h=None):
    return Citation(label, quote, hashlib.sha256(quote.encode()).hexdigest() if h is None else h)


BUNDLE = {"E1": "the alpha fact is stated here", "E2": "beta fact lives elsewhere"}


def test_citation_valid():
    assert check_citation(_cit(), BUNDLE) is None
    v = citation_validity([_cit(), _cit("E2", "beta fact")], BUNDLE)
    assert v["valid"] == 2 and v["total"] == 2 and v["rate"] == 1.0 and v["failures"] == []


def test_citation_bad_label_bad_hash_span_outside_chunk():
    assert check_citation(_cit("E9"), BUNDLE) == "label_not_in_turn"
    assert check_citation(_cit(h="0" * 64), BUNDLE) == "quote_hash_mismatch"
    assert check_citation(_cit(quote="gamma fact"), BUNDLE) == "quote_not_in_chunk"
    assert check_citation(_cit(quote="alpha fact"), {"E1": "beta only"}) == "quote_not_in_chunk"
    v = citation_validity([_cit(), _cit("E9"), _cit(h="1" * 64), _cit(quote="gamma fact")], BUNDLE)
    assert v["total"] == 4 and v["valid"] == 1 and v["rate"] == 0.25
    assert [f["code"] for f in v["failures"]] == ["label_not_in_turn", "quote_hash_mismatch", "quote_not_in_chunk"]
    assert citation_validity([], BUNDLE)["rate"] is None


def test_citation_check_reuses_verification_hash():
    from scribe_intel.verification import sha256_hex

    assert sha256_hex("alpha fact") == hashlib.sha256(b"alpha fact").hexdigest()
    assert check_citation(Citation("E1", "alpha fact", sha256_hex("alpha fact")), BUNDLE) is None


# ---------------------------------------------------------------- latency

def test_percentiles_nearest_rank():
    vals = [float(i) for i in range(1, 101)]
    assert percentile(vals, 0.50) == 50.0 and percentile(vals, 0.95) == 95.0 and percentile(vals, 1.0) == 100.0
    assert percentile([7.0], 0.95) == 7.0 and percentile([], 0.5) is None
    assert percentile([3, 1, 2, 4], 0.5) == 2.0


def test_latency_summary_includes_embed_and_marks_live_embeds():
    rows = [{"latency_ms": 100 + i, "embed_ms": 40.0 + i, "retrieval_ms": 60.0, "embed_cached": i % 2 == 0}
            for i in range(10)]
    s = latency_summary(rows)
    assert s["n"] == 10 and s["total_p50_ms"] == 104 and s["total_p95_ms"] == 109
    assert s["embed_p50_ms"] == 44 and s["retrieval_p95_ms"] == 60
    assert s["embed_live_n"] == 5 and s["embed_timed_n"] == 10
    lex = latency_summary([{"latency_ms": 5.0, "embed_cached": None}])
    assert lex["embed_timed_n"] == 0 and lex["embed_p50_ms"] is None


# ---------------------------------------------------------------- price table and cost math

def test_price_table_entries_are_dated_and_marked_to_verify():
    from scribe_ingest.embedding import DEFAULT_MODEL

    ids = {p.model_id for p in cost.PRICES}
    assert {"gemini-embedding-001", DEFAULT_MODEL, "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite",
            "rerank-2.5"} <= ids
    for p in cost.PRICES:
        assert "2026-10-20" in p.source_note and "TO VERIFY" in p.source_note and p.usd > 0 and p.unit
    for m in ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"):
        assert cost.price_usd(m, cost.UNIT_INPUT) < cost.price_usd(m, cost.UNIT_OUTPUT)


def test_cost_math_and_fake_models_are_free():
    flash_in, flash_out = cost.price_usd("gemini-2.5-flash", cost.UNIT_INPUT), cost.price_usd("gemini-2.5-flash", cost.UNIT_OUTPUT)
    assert cost.tokens_usd("gemini-2.5-flash", 1_000_000, 1_000_000) == pytest.approx(flash_in + flash_out)
    assert cost.tokens_usd("gemini-2.5-flash", 500_000, 0) == pytest.approx(flash_in / 2)
    assert cost.tokens_usd("rerank-2.5", 2_000_000) == pytest.approx(2 * cost.price_usd("rerank-2.5", cost.UNIT_TOKENS))
    assert cost.tokens_usd("fake-embedder", 10**9) == 0.0
    with pytest.raises(cost.UnknownPrice):
        cost.tokens_usd("not-a-model", 1)


def test_price_per_query_sums_parts_and_cached_parts_cost_zero():
    embed = cost.Usage("gemini-embedding-001", 1000, estimated=True)
    rerank = cost.Usage("rerank-2.5", 20_000, estimated=True)
    gen = cost.usage_from_provider("gemini-2.5-flash", 2000, 300)  # provider-reported: exact
    judge = cost.Usage("gemini-2.5-pro", 500, 50, estimated=True)
    q = cost.price_per_query(embed=embed, rerank=rerank, generation=gen, judge=judge)
    expect = (1000 * 0.15 + 20_000 * 0.05 + 2000 * 0.30 + 300 * 2.50 + 500 * 1.25 + 50 * 10.0) / 1_000_000
    assert q.usd == pytest.approx(expect) and q.estimated is True and q.cached is False
    assert not cost.price_per_query(generation=gen).estimated
    cached = cost.price_per_query(embed=cost.Usage("gemini-embedding-001", 1000, cached=True), generation=gen)
    assert cached.usd == pytest.approx(gen.usd) and cached.list_usd > cached.usd and cached.cached is False
    allcached = cost.price_per_query(embed=cost.Usage("gemini-embedding-001", 1000, cached=True))
    assert allcached.usd == 0.0 and allcached.cached is True and allcached.as_json()["usd"] == 0.0


def test_estimated_tokens_use_tiktoken_when_provider_gives_none():
    u = cost.usage_from_provider("gemini-2.5-flash", None, None, fallback_input_text="hello world " * 50,
                                 fallback_output_text="answer text")
    assert u.estimated and u.input_tokens == cost.estimate_tokens("hello world " * 50) > 50 and u.output_tokens > 0


def test_cost_summary():
    rows = [cost.price_per_query(embed=cost.Usage("gemini-embedding-001", 1_000_000)).as_json(),
            cost.price_per_query(embed=cost.Usage("gemini-embedding-001", 1_000_000, cached=True)).as_json()]
    s = cost.cost_summary(rows)
    assert s["queries"] == 2 and s["usd"] == pytest.approx(0.15) and s["usd_per_query"] == pytest.approx(0.075)
    assert s["cached_queries"] == 1 and s["estimated"] is True


# ---------------------------------------------------------------- ledger and cap

def test_ledger_append_total_and_line_shape(tmp_path):
    led = tmp_path / "l.jsonl"
    assert cost.spend_total(led) == 0.0
    cost.append_spend(run_id="r1", kind="run-profile", model_ids=["rerank-2.5"], tokens={"rerank-2.5:input": 5},
                      usd=1.25, estimated=True, live_calls=2, path=led)
    cost.append_spend(run_id="r2", kind="answer", model_ids={"generator": "gemini-2.5-flash"}, tokens={}, usd=0.75,
                      estimated=False, live_calls=1, path=led)
    assert cost.spend_total(led) == pytest.approx(2.0)
    rows = [json.loads(l) for l in led.read_text().splitlines()]
    assert set(rows[0]) >= {"ts", "run_id", "kind", "model_ids", "tokens", "usd", "estimated"} and len(rows) == 2


def test_cap_is_one_constant_and_can_only_be_lowered():
    assert cost.CAP_USD == 10.00 and cost.resolve_cap(None) == 10.00
    assert cost.resolve_cap(3.5) == 3.5 and cost.resolve_cap(10.0) == 10.0 and cost.resolve_cap(0) == 0
    for bad in (10.01, 50, float("inf"), float("nan"), -1):
        with pytest.raises(cost.SpendError):
            cost.resolve_cap(bad)


def test_preflight_refuses_over_cap_and_allows_under(tmp_path):
    led = tmp_path / "l.jsonl"
    cost.append_spend(run_id="r", kind="x", model_ids=[], tokens={}, usd=9.0, estimated=False, live_calls=1, path=led)
    assert cost.preflight(0.99, path=led) == (pytest.approx(9.0), 10.0)
    with pytest.raises(cost.SpendError, match="exceeds"):
        cost.preflight(1.01, path=led)
    with pytest.raises(cost.SpendError, match="exceeds"):
        cost.preflight(0.5, spend_cap=9.2, path=led)  # a lowered cap applies
    with pytest.raises(cost.SpendError, match="raise the cap"):
        cost.preflight(0.0, spend_cap=11, path=led)


def test_cached_calls_are_recorded_as_zero_dollars(tmp_path):
    led = tmp_path / "l.jsonl"
    spend = cost.RunSpend("run-profile", "r1")
    spend.add(cost.Usage("gemini-embedding-001", 5000, estimated=True, cached=True), calls=10)
    rec = spend.write(led)
    assert rec["usd"] == 0.0 and rec["cached"] is True and rec["cached_calls"] == 10 and rec["live_calls"] == 0
    assert cost.spend_total(led) == 0.0
    mixed = cost.RunSpend("run-profile", "r2")
    mixed.add(cost.Usage("gemini-embedding-001", 1_000_000, estimated=True))
    mixed.add(cost.Usage("gemini-embedding-001", 1_000_000, estimated=True, cached=True))
    rec = mixed.write(led)
    assert rec["usd"] == pytest.approx(0.15) and rec["cached"] is False and rec["cached_calls"] == 1 and rec["live_calls"] == 1
    assert cost.append_spend(run_id="r3", kind="x", model_ids=[], tokens={}, usd=5.0, estimated=False, cached_calls=2,
                             path=led)["usd"] == 0.0  # a fully cached line can never carry dollars


def test_damaged_ledger_blocks_spending(tmp_path):
    led = tmp_path / "l.jsonl"
    led.write_text('{"usd": 1.0}\nnot json\n')
    with pytest.raises(cost.SpendError, match="unreadable"):
        cost.spend_total(led)

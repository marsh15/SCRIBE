import asyncio
import json
import math
import os
import subprocess
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from scribe_eval import cache as cache_mod
from scribe_eval.bootstrap import not_worse, paired_bootstrap
from scribe_eval.cache import CacheMiss, DiskCache, embedding_key, rerank_key, text_hash
from scribe_eval.cli import main
from scribe_eval.corpus import load_document_text
from scribe_eval.dataset import Case, load_cases
from scribe_eval.metrics import METRIC_NAMES, RankedItem, case_metrics, evaluate_case, summarize, top_score
from scribe_eval.normalize import normalize
from scribe_eval.runs import RunRecord, load_run, persist_to_db
from scribe_eval.scoring import ScoreError, score_split

REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / "benchmarks" / "datasets" / "golden-v1"
CORPUS = REPO / "fixtures" / "corpus"


def mkcase(quotes_grades, cid="c1", answerable=True, category="x", split="dev"):
    ev = [{"doc": "d.md", "version": 1, "quote": q, "page": None, "grade": g} for q, g in quotes_grades]
    return Case.model_validate({
        "id": cid, "split": split, "category": category, "tags": [], "question": "q", "answerable": answerable,
        "reference_answer": "r", "must_include": [], "evidence": ev, "forbidden": []})


def items(*texts):
    return [RankedItem(chunk_id=str(i), text=t) for i, t in enumerate(texts)]


def test_hand_calculated_fixture():
    # Evidence: A (grade 2), B (grade 1), C (grade 2). Ranking:
    #   1: irrelevant, 2: contains A and B, 3: contains C, 4: irrelevant
    case = mkcase([("alpha", 2), ("beta", 1), ("gamma", 2)])
    m = case_metrics(case, items("zzz", "Alpha and BETA", "gamma!", "zzz"))
    # recall@1 = 0/3; recall@5 = 3/3; recall@10 = 3/3 ; recall@2 = n/a
    assert m["recall@1"] == 0 and m["recall@5"] == 1 and m["recall@10"] == 1
    assert m["hit@1"] == 0 and m["hit@5"] == 1
    assert m["mrr"] == 0.5  # first relevant at rank 2
    assert m["precision@5"] == pytest.approx(2 / 5)  # items 2 and 3 relevant
    # DCG: item2 newly credits A(2),B(1) -> gain 2 at rank 2: 2/log2(3) = 1.261860
    #      item3 credits C(2) at rank 3: 2/log2(4) = 1.0 -> DCG = 2.261860
    # IDCG grades [2,2,1]: 2/1 + 2/log2(3) + 1/log2(4) = 2 + 1.261860 + 0.5 = 3.761860
    assert m["ndcg@10"] == pytest.approx(2.261860 / 3.761860, abs=1e-5)  # 0.60127


def test_partial_recall_at_k():
    # Two entries; top-1 finds one of them: recall@1 = 1/2, recall@5 = 2/2.
    case = mkcase([("alpha", 2), ("beta", 2)])
    m = case_metrics(case, items("alpha", "x", "beta"))
    assert (m["recall@1"], m["recall@5"], m["hit@1"], m["mrr"]) == (0.5, 1.0, 1.0, 1.0)


def test_unanswerable_excluded_and_reported():
    ua = mkcase([], cid="u1", answerable=False, category="unanswerable")
    ok = mkcase([("alpha", 2)], cid="a1")
    r1 = evaluate_case(ua, {"case_id": "u1", "ranking": [{"text": "t", "score": 0.7}]})
    r2 = evaluate_case(ok, {"case_id": "a1", "ranking": [{"text": "alpha"}]})
    s = summarize([r1, r2])
    assert s["overall"].n == 1 and s["overall"].mean["mrr"] == 1.0
    assert s["unanswerable"]["count"] == 1 and s["unanswerable"]["max_top_score"] == 0.7
    assert top_score(items("a")) is None
    with pytest.raises(ValueError):
        case_metrics(ua, [])


def test_summary_ci_brackets_mean():
    cases = [mkcase([("alpha", 2)], cid=f"c{i}") for i in range(10)]
    rs = [evaluate_case(c, {"case_id": c.id, "ranking": [{"text": "alpha" if i < 6 else "no"}]}) for i, c in enumerate(cases)]
    agg = summarize(rs)["overall"]
    lo, hi = agg.ci95["hit@1"]
    assert agg.mean["hit@1"] == 0.6 and lo <= 0.6 <= hi


WORDS = st.sampled_from(["alpha", "bravo", "gamma", "delta", "kappa", "omega", "sigma", "lambda"])


@st.composite
def scenario(draw):
    quotes = draw(st.lists(WORDS, min_size=1, max_size=4, unique=True))
    grades = [draw(st.sampled_from([1, 2])) for _ in quotes]
    texts = [" ".join(draw(st.lists(WORDS, max_size=3))) + " filler" for _ in range(draw(st.integers(0, 14)))]
    return mkcase(list(zip(quotes, grades))), texts


@settings(max_examples=150, deadline=None)
@given(scenario())
def test_property_bounds(sc):
    case, texts = sc
    for v in case_metrics(case, items(*texts)).values():
        assert 0.0 <= v <= 1.0


@settings(max_examples=100, deadline=None)
@given(scenario())
def test_property_perfect_ranking(sc):
    case, _ = sc
    # Best case: entries sorted by grade desc, one item each.
    order = sorted(case.evidence, key=lambda e: -e.grade)
    m = case_metrics(case, items(*[e.quote for e in order]))
    assert m["recall@10"] == 1.0 and m["mrr"] == 1.0 and m["ndcg@10"] == pytest.approx(1.0)


@settings(max_examples=150, deadline=None)
@given(scenario(), st.data())
def test_property_removing_hit_never_increases_recall(sc, data):
    case, texts = sc
    full = items(*texts)
    hits = [i for i, t in enumerate(texts) if any(normalize(e.quote) in normalize(t) for e in case.evidence)]
    if not hits:
        return
    drop = data.draw(st.sampled_from(hits))
    reduced = full[:drop] + full[drop + 1:]
    a, b = case_metrics(case, full), case_metrics(case, reduced)
    for k in (1, 5, 10):
        # removing an item can only shift later items up; replace it with an irrelevant one instead to compare fairly
        replaced = full[:drop] + [RankedItem("x", "zzz filler")] + full[drop + 1:]
        assert case_metrics(case, replaced)[f"recall@{k}"] <= a[f"recall@{k}"] + 1e-12
    assert b is not None


def test_bootstrap_deterministic_and_zero_for_identical():
    a = {f"c{i}": (i % 3) / 2 for i in range(30)}
    b = {k: min(1.0, v + 0.2) for k, v in a.items()}
    r1, r2 = paired_bootstrap(a, b), paired_bootstrap(a, b)
    assert r1 == r2 and r1.mean_diff == pytest.approx(0.2, abs=0.1) and r1.n == 30
    z = paired_bootstrap(a, dict(a))
    assert (z.mean_diff, z.lo, z.hi) == (0.0, 0.0, 0.0)
    assert not_worse(z) and not not_worse(paired_bootstrap(b, a))
    assert not_worse(paired_bootstrap(b, a), margin=0.5)
    assert paired_bootstrap({}, {}).n == 0 and not not_worse(paired_bootstrap({}, {}))


@settings(max_examples=30, deadline=None)
@given(st.lists(st.floats(0, 1), min_size=1, max_size=20))
def test_property_bootstrap_identical_zero(vals):
    d = {str(i): v for i, v in enumerate(vals)}
    r = paired_bootstrap(d, d, n_resamples=200)
    assert r.mean_diff == 0 and r.lo == 0 and r.hi == 0


# ---- cache ----
def test_cache_keys():
    assert embedding_key("m", 3, "doc", "x") == embedding_key("m", 3, "doc", "x")
    assert embedding_key("m", 3, "doc", "x") != embedding_key("m", 4, "doc", "x")
    assert rerank_key("r", "q", [text_hash("a")]) != rerank_key("r", "q", [text_hash("b")])


async def test_cache_hit_miss_and_persistence(tmp_path):
    c = DiskCache(tmp_path / "c.sqlite", mode="record")
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        return [0.1, 0.2]

    assert await c.get_or_compute("k", fn) == [0.1, 0.2]
    assert await c.get_or_compute("k", fn) == [0.1, 0.2]
    assert calls == 1 and c.stats == {"hits": 1, "misses": 1}
    c.close()
    r = DiskCache(tmp_path / "c.sqlite", mode="replay")
    assert await r.get_or_compute("k", fn) == [0.1, 0.2]
    with pytest.raises(CacheMiss, match="Refresh the cache locally"):
        await r.get_or_compute("other", fn)
    assert calls == 1


async def test_cache_env_mode_and_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_CACHE_MODE", "replay")
    c = DiskCache(tmp_path / "c.sqlite")
    assert c.mode == "replay"
    with pytest.raises(CacheMiss):
        await c.get_or_compute("k", lambda: asyncio.sleep(0))
    with pytest.raises(ValueError):
        DiskCache(tmp_path / "d.sqlite", mode="bogus")


async def test_cache_concurrency_limit(tmp_path):
    c = DiskCache(tmp_path / "c.sqlite", mode="record", concurrency=2)
    running = peak = 0

    async def fn():
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return 1

    await asyncio.gather(*[c.get_or_compute(f"k{i}", fn) for i in range(8)])
    assert peak == 2


# ---- runs ----
def test_run_record_has_inv11_fields(tmp_path):
    run = RunRecord(dataset_version="golden-v1", split="dev", profile_name="p", config_hash="h",
                    model_ids={"embed": "m"}, prompt_versions={"answer": "v1"})
    d = run_dir = run.write(tmp_path)
    data = json.loads((d / "run.json").read_text())
    for f in ("dataset_version", "split", "profile_name", "config_hash", "code_commit", "model_ids",
              "prompt_versions", "tool_versions", "started_at", "finished_at", "run_id"):
        assert data[f] not in (None, ""), f
    assert (d / "results.jsonl").exists() and run_dir.parent == tmp_path
    assert isinstance(data["code_commit"], str)


def test_git_commit_unknown_outside_repo(tmp_path, monkeypatch):
    from scribe_eval.runs import git_commit
    assert git_commit(tmp_path / "nonexistent") == "unknown"


@pytest.mark.skipif(not os.environ.get("DATABASE_URL_TEST"), reason="DATABASE_URL_TEST not set")
def test_persist_to_db():
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL_TEST"]) as conn:
        have = {r[0] for r in conn.execute(
            "select table_name from information_schema.tables where table_name in ('eval_runs','eval_results')")}
        if have != {"eval_runs", "eval_results"}:
            pytest.skip("eval_runs/eval_results tables not present in DATABASE_URL_TEST db (T08 migration not applied)")
        run = RunRecord(dataset_version="golden-v1", split="dev", profile_name="p", config_hash="h",
                        case_results=[{"case_id": "sf-01", "metrics": {"mrr": 1.0}, "ranking": [], "latency_ms": 12.4, "cost": None}])
        run.finished_at = run.started_at
        persist_to_db(conn, run)
        n = conn.execute("select count(*) from eval_results where run_id = %s::uuid", (run.run_id,)).fetchone()[0]
        assert n == 1
        conn.rollback()


# ---- CLI / end to end ----
def oracle_rankings(path: Path, splits=("dev", "validation", "test")):
    cases = load_cases(DATASET / "cases.jsonl")
    texts: dict[str, list[str]] = {}
    lines = []
    for c in cases:
        if c.split not in splits:
            continue
        ranking = []
        for e in sorted(c.evidence, key=lambda e: -e.grade):
            doc_lines = texts.setdefault(e.doc, load_document_text(CORPUS / e.doc).splitlines())
            q = normalize(e.quote)
            line = next((ln for ln in doc_lines if q in normalize(ln)), e.quote)
            ranking.append({"chunk_id": f"{e.doc}:{len(ranking)}", "doc": e.doc, "text": line, "score": 1.0, "method": "oracle"})
        if not c.answerable:
            ranking = [{"chunk_id": "n", "doc": "x", "text": "irrelevant", "score": 0.3, "method": "oracle"}]
        lines.append(json.dumps({"case_id": c.id, "ranking": ranking, "latency_ms": 1.0, "cost": None, "degraded": None}))
    path.write_text("\n".join(lines) + "\n")


def test_cli_refuses_test_split(tmp_path, capsys):
    rk = tmp_path / "r.jsonl"
    rk.write_text("")
    rc = main(["score", "--rankings", str(rk), "--dataset", str(DATASET), "--split", "test", "--out", str(tmp_path / "o")])
    assert rc == 2 and "INV-12" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_cli_end_to_end_oracle(tmp_path, capsys):
    rk = tmp_path / "r.jsonl"
    oracle_rankings(rk)
    out = tmp_path / "runs"
    for split, extra in (("dev", []), ("validation", []), ("test", ["--milestone-report"])):
        rc = main(["score", "--rankings", str(rk), "--dataset", str(DATASET), "--split", split,
                   "--profile", "oracle", "--out", str(out), *extra])
        assert rc == 0, capsys.readouterr().err
    runs = sorted(out.iterdir())
    assert len(runs) == 3
    for d in runs:
        run, rows = load_run(d)
        ans = [r for r in rows if r["answerable"]]
        assert ans and run["code_commit"] and run["dataset_version"] == "golden-v1"
        for r in ans:
            assert r["metrics"]["hit@1"] == 1.0 and r["metrics"]["recall@10"] == 1.0 and r["metrics"]["mrr"] == 1.0
        # recall@1 is 1.0 whenever a case has a single evidence entry
        single = [r for r in ans if r["metrics"]["recall@10"] == 1.0 and len(r["ranking"]) == 1]
        assert all(r["metrics"]["recall@1"] == 1.0 for r in single)
        assert run["summary"]["overall"]["mean"]["hit@1"] == 1.0
        assert run["summary"]["unanswerable"]["count"] > 0 or run["split"] == "validation"
    capsys.readouterr()
    assert main(["compare", str(runs[0]), str(runs[0])]) == 0
    assert "mrr" in capsys.readouterr().out


def test_missing_cases_is_error(tmp_path, capsys):
    rk = tmp_path / "r.jsonl"
    oracle_rankings(rk, splits=("dev",))
    lines = rk.read_text().splitlines()
    missing_id = json.loads(lines[0])["case_id"]
    rk.write_text("\n".join(lines[1:]) + "\n")
    with pytest.raises(ScoreError, match=missing_id):
        score_split(DATASET, "dev", rk)
    rc = main(["score", "--rankings", str(rk), "--dataset", str(DATASET), "--split", "dev", "--out", str(tmp_path / "o")])
    assert rc == 2 and missing_id in capsys.readouterr().err


def test_csv_quote_matches_rendered_rows_in_any_pipeline():
    """CSV labels are raw lines; legacy (space-joined) and 2.0 (col: val) renderings both match."""
    from scribe_eval.dataset import Case
    from scribe_eval.metrics import RankedItem, match_matrix

    case = Case.model_validate({
        "id": "t", "split": "dev", "category": "table_lookup", "tags": [], "question": "q",
        "answerable": True, "reference_answer": "a", "must_include": [],
        "evidence": [{"doc": "fleet.csv", "version": 1, "quote": "NW-T-0417,Hamburg,Volvo FH,2022", "page": None, "grade": 2}],
        "forbidden": [],
    })
    legacy = RankedItem(chunk_id="1", doc="fleet.csv", text="id city\nNW-T-0417 Hamburg Volvo FH 2022\nNW-T-0418 Lyon")
    new = RankedItem(chunk_id="2", doc="fleet.csv", text="Table: fleet.csv · Row 3 · id: NW-T-0417 · city: Hamburg · model: Volvo FH · year: 2022")
    wrong_order = RankedItem(chunk_id="3", doc="fleet.csv", text="2022 Volvo FH Hamburg NW-T-0417")
    other = RankedItem(chunk_id="4", doc="fleet.csv", text="NW-T-0418 Lyon Volvo FH 2022")
    assert match_matrix(case, [legacy, new, wrong_order, other]) == [{0}, {0}, set(), set()]

"""T28: 200-case golden set, answer-level scoring, judge agreement. No provider is ever called: every generator,
verifier and judge here is a clearly stamped fake or a scripted stub, and httpx traffic is a MockTransport."""

import json
import math
from collections import Counter
from pathlib import Path

import httpx
import pytest

from scribe_eval import answer as ans
from scribe_eval import judge as jd
from scribe_eval.cli import main
from scribe_eval.dataset import Case, load_cases
from scribe_eval.normalize import normalize
from scribe_eval.splits import assign_splits
from scribe_eval.validate import validate_dataset

REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / "benchmarks" / "datasets" / "golden-v1"
CORPUS = REPO / "fixtures" / "corpus"
NEW = "machine-drafted-t28"


# ------------------------------------------------------------------ the dataset

@pytest.fixture(scope="module")
def golden() -> list[Case]:
    return load_cases(DATASET / "cases.jsonl")


def test_validate_passes_on_200_cases():
    cases, errors = validate_dataset(DATASET, CORPUS)
    assert errors == []
    assert len(cases) == 200


def test_category_and_split_proportions(golden):
    cats = Counter(c.category for c in golden)
    assert len(cats) == 10 and set(cats.values()) == {20}
    splits = Counter(c.split for c in golden)
    for name, want in (("dev", 0.4), ("validation", 0.2), ("test", 0.4)):
        assert abs(splits[name] / 200 - want) <= 0.05
    assert (splits["dev"], splits["validation"], splits["test"]) == (80, 40, 80)
    for cat in cats:
        per = Counter(c.split for c in golden if c.category == cat)
        assert (per["dev"], per["validation"], per["test"]) == (8, 4, 8)
    assert sum(1 for c in golden if not c.answerable) == 20


def test_new_cases_are_marked_machine_drafted_and_unreviewed(golden):
    new = [c for c in golden if c.origin == NEW]
    assert len(new) == 80
    assert all(c.reviewed is False for c in new)
    assert Counter(c.category for c in new) == Counter({c.category: 8 for c in golden})
    old = [c for c in golden if c.origin is None]
    assert len(old) == 120 and all(c.reviewed is None for c in old)
    # the 120 original splits are untouched (50 dev / 20 validation / 50 test)
    assert Counter(c.split for c in old) == Counter({"dev": 50, "validation": 20, "test": 50})
    # abstention (empty evidence) and conflict cases are present among the new ones
    assert sum(1 for c in new if not c.answerable and not c.evidence) == 8
    assert sum(1 for c in new if c.category == "version_conflict") == 8


def test_review_docs_list_every_unreviewed_case(golden):
    text = (DATASET / "UNREVIEWED_T28.md").read_text(encoding="utf-8")
    for c in golden:
        if c.origin == NEW:
            assert f"`{c.id}`" in text, c.id
    assert "UNREVIEWED_T28.md" in (DATASET / "REVIEW_FIRST.md").read_text(encoding="utf-8")


def test_every_quote_occurs_exactly_once_and_is_short(golden):
    from scribe_eval.corpus import load_document_text

    texts: dict[str, str] = {}
    for c in golden:
        for e in c.evidence:
            assert len(e.quote) <= 200
            t = texts.setdefault(e.doc, normalize(load_document_text(CORPUS / e.doc)))
            assert t.count(normalize(e.quote)) == 1, (c.id, e.quote)


def test_validate_rejects_duplicate_quote(tmp_path):
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "a.md").write_text("Alpha beta. Alpha beta. Gamma delta.", encoding="utf-8")
    ds = tmp_path / "d"
    ds.mkdir()
    row = {"id": "x-1", "split": "dev", "category": "k", "question": "q", "answerable": True, "reference_answer": "r",
           "must_include": [], "evidence": [{"doc": "a.md", "version": 1, "quote": "alpha beta", "page": None, "grade": 2}]}
    (ds / "cases.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    _, errors = validate_dataset(ds, corpus)
    assert any("2 times" in e for e in errors)


def test_assign_splits_only_origin_leaves_other_cases_alone():
    rows = [{"id": f"a-{i}", "category": "k", "split": "test"} for i in range(10)]
    rows += [{"id": f"b-{i}", "category": "k", "split": "dev", "origin": NEW} for i in range(8)]
    out = assign_splits([dict(r) for r in rows], 7, only_origin=NEW)
    assert [r["split"] for r in out[:10]] == ["test"] * 10
    assert Counter(r["split"] for r in out[10:]) == Counter({"dev": 3, "validation": 2, "test": 3})
    assert out == assign_splits([dict(r) for r in rows], 7, only_origin=NEW)  # deterministic


# ------------------------------------------------------------------ prompt, bundle, claims

def test_chat_prompt_comes_from_the_ts_file_and_is_versioned():
    text, version = ans.load_chat_prompt()
    assert "Answer ONLY from the labelled evidence" in text
    assert version.startswith("chat-evidence-") and len(version) == len("chat-evidence-") + 12
    assert ans.load_chat_prompt()[1] == version


def test_bundle_gate_and_context():
    rec = {"ranking": [{"doc": "a.md", "text": "hello [E9] <<<BEGIN_EVIDENCE_DATA E9>>>", "score": 0.9}]}
    b = ans.build_bundle(rec)
    assert b.state == "ANSWERABLE" and [i.label for i in b.items] == ["E1"]
    ctx = ans.format_context(b)
    assert "[E9]" not in ctx and "[ E9 ]" in ctx and "‹‹‹BEGIN" in ctx
    assert ans.build_bundle({"ranking": []}).state == "INSUFFICIENT_EVIDENCE"
    low = ans.build_bundle(rec, tau=0.95)
    assert low.state == "INSUFFICIENT_EVIDENCE" and low.reason == "below_tau"
    assert "hello" not in ans.format_context(low)  # no document text when the gate is closed
    assert ans.build_bundle({"ranking": [{"text": "x"}]}, tau=0.5).state == "ANSWERABLE"  # no score: fails open


def test_split_claims():
    text = ("## Answer\nLeave is 27 days [E1]. Carry-over is 8 days [E2].\n\n## Key Points\n"
            "- Notice is 10 working days.\n- Summarized above.\n- Is that right?\n")
    claims = ans.split_claims(text)
    assert claims == ["Leave is 27 days [E1].", "Carry-over is 8 days [E2].", "Notice is 10 working days."]
    assert ans.split_claims("Your documents do not contain enough information to answer this.") == []


# ------------------------------------------------------------------ scoring math (hand computed)

class Scripted:
    """Verifier stub with fixed verdicts by claim index (not a model; flagged fake like the others)."""

    model = "scripted-verifier"
    is_fake = True

    def __init__(self, verdicts):
        self.verdicts = verdicts

    def verify(self, claims, quotes):
        return {i: self.verdicts[i] for i, _, _ in claims if i in self.verdicts}


def make_case(cid="c1", category="k", answerable=True, evidence=None, must=None, ref="ref", forbidden=None):
    if evidence is None:
        evidence = [{"doc": "a.md", "version": 1, "quote": "alpha one", "page": None, "grade": 2},
                    {"doc": "b.md", "version": 1, "quote": "beta two", "page": None, "grade": 1}] if answerable else []
    return Case.model_validate({"id": cid, "split": "dev", "category": category, "question": "q?", "answerable": answerable,
                                "reference_answer": ref, "must_include": must or [], "evidence": evidence,
                                "forbidden": forbidden or []})


def bundle_for(*items):
    return ans.Bundle("ANSWERABLE", "ok", [ans.EvidenceItem(f"E{i}", d, t) for i, (d, t) in enumerate(items, 1)], 0.9, 0.0)


def test_precision_recall_groundedness_hand_computed():
    case = make_case(must=["27 days"])
    bundle = bundle_for(("a.md", "xx Alpha One yy"), ("c.md", "noise"), ("b.md", "Beta Two"))
    answer = ("## Answer\nLeave is 27 days [E1]. Noise fact here [E2]. Beta fact stated [E3]. "
              "An unsupported claim [E1]. Bare claim without label.\n")
    claims = ans.split_claims(answer)
    assert len(claims) == 5
    # verdicts by claim index: 1 SUPPORTED(E1), 2 PARTIAL(E2), 3 UNSUPPORTED(E3), 4 UNSUPPORTED(E1), 5 uncited
    out, state = ans.verify_claims_local(claims, bundle, Scripted({1: "SUPPORTED", 2: "PARTIAL", 3: "UNSUPPORTED", 4: "UNSUPPORTED"}))
    assert [c.status for c in out] == ["VERIFIED", "PARTIAL", "UNSUPPORTED", "UNSUPPORTED", "UNCITED"] and state == "ok"
    r = ans.score_case(case, bundle, answer, out)
    # verified/partial cited labels: E1 (matches gold alpha) and E2 (noise) -> precision 1/2; gold covered: 1 of 2
    assert r.cited_labels == ["E1", "E2"]
    assert r.metrics["citation_precision"] == 0.5
    assert r.metrics["citation_recall"] == 0.5
    assert r.metrics["groundedness"] == 2 / 5
    assert r.metrics["correct"] == 1.0 and r.metrics["must_include_frac"] == 1.0
    assert r.abstained is False


def test_invalid_citation_uncited_and_unverified_statuses():
    bundle = bundle_for(("a.md", "alpha one"))
    claims = ["Forged label [E7].", "Real [E1].", "No label claim here."]
    out, _ = ans.verify_claims_local(claims, bundle, None)
    assert [c.status for c in out] == ["INVALID_CITATION", "UNVERIFIED", "UNCITED"]
    out, state = ans.verify_claims_local(["Real [E1]."], bundle, Scripted({}))
    assert out[0].status == "UNVERIFIED" and state == "partial"

    class Boom(Scripted):
        def verify(self, claims, quotes):
            raise RuntimeError("provider said secret-text")

    out, state = ans.verify_claims_local(["Real [E1]."], bundle, Boom({}))
    assert out[0].status == "UNVERIFIED" and state == "error:RuntimeError" and "secret" not in state


def test_no_citation_gives_none_precision_zero_recall():
    case = make_case()
    r = ans.score_case(case, bundle_for(("a.md", "alpha one")), "Some answer.", [])
    assert r.metrics["citation_precision"] is None and r.metrics["citation_recall"] == 0.0
    assert r.metrics["groundedness"] is None


def test_wrong_version_citation_is_counted():
    case = make_case(forbidden=[{"doc": "old.md"}], evidence=[{"doc": "new.md", "version": 1, "quote": "twenty seven", "page": None, "grade": 2}])
    bundle = bundle_for(("old.md", "twenty seven"), ("new.md", "twenty seven"))
    out, _ = ans.verify_claims_local(["A [E1].", "B [E2]."], bundle, Scripted({1: "SUPPORTED", 2: "SUPPORTED"}))
    r = ans.score_case(case, bundle, "A [E1]. B [E2].", out)
    assert r.metrics["wrong_version_citations"] == 1.0


def test_must_include_exact_and_normalisation():
    assert ans.must_include_hits("The cap is EUR 2000000 [E1].", ["2,000,000"]) == [True]
    assert ans.must_include_hits("Cap: EUR 2,000,000", ["2000000", "nope"]) == [True, False]
    assert ans.must_include_hits("It’s 09:00", ["09:00"]) == [True]
    assert ans.exact_match("## Answer\n- The 18 months. [E1]\n", "The 18 months.") is True
    assert ans.exact_match("different", "The 18 months.") is False
    case = make_case(must=["a", "zzz"])
    r = ans.score_case(case, bundle_for(("a.md", "alpha one")), "a only", [])
    assert r.metrics["must_include_frac"] == 0.5 and r.metrics["correct"] == 0.0 and r.metrics["exact_match"] == 0.0


def test_abstention_detection_and_matrix():
    refusal = "## Answer\nYour documents do not contain enough information to answer this.\n"
    assert ans.is_abstention(refusal, [])
    assert not ans.is_abstention("Leave is 27 days [E1]. The documents do not cover part-time.", [])
    assert not ans.is_abstention("Leave is 27 days.", [])
    ua = make_case("u1", answerable=False)
    an = make_case("a1", must=["27"])
    b = bundle_for(("a.md", "alpha one"))
    results = [
        ans.score_case(ua, b, refusal, []),                      # unanswerable, abstained: correct
        ans.score_case(make_case("u2", answerable=False), b, "It is 9 [E1].", []),  # hallucination
        ans.score_case(an, b, refusal, []),                      # answerable, abstained: false abstain
        ans.score_case(make_case("a2", must=["27"]), b, "27 days [E1].", []),  # answerable answered
        ans.score_case(make_case("a3", must=["27"]), b, "27 days [E1].", []),
    ]
    s = ans.summarize_answers(results)
    assert s["abstention"]["matrix"] == {"answerable_answered": 2, "answerable_abstained": 1,
                                         "unanswerable_answered": 1, "unanswerable_abstained": 1}
    assert s["abstention"]["false_abstain_rate"] == pytest.approx(1 / 3)
    assert s["abstention"]["abstention_recall"] == 0.5 and s["abstention"]["hallucination_rate"] == 0.5
    # correctness (unanswerable: correct = abstained): u1 yes, u2 no, a1 no, a2 yes, a3 yes = 3/5, with a bootstrap interval
    assert s["overall"]["correct"]["n"] == 5 and s["overall"]["correct"]["mean"] == pytest.approx(0.6)
    lo, hi = s["overall"]["correct"]["ci95"]
    assert lo <= 0.6 <= hi
    assert set(s["per_category"]) == {"k"}


# ------------------------------------------------------------------ kappa

def test_kappa_known_values():
    assert jd.cohens_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    # textbook 2x2: 20 yy, 5 yn, 10 ny, 15 nn -> po .7, pe .5, kappa .4
    a = ["y"] * 25 + ["n"] * 25
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert jd.cohens_kappa(a, b) == pytest.approx(0.4)
    assert jd.cohens_kappa(["x", "x", "y", "y"], ["x", "y", "x", "y"]) == pytest.approx(0.0)
    assert jd.cohens_kappa(["x", "x", "y", "y"], ["y", "y", "x", "x"]) == pytest.approx(-1.0)
    assert math.isnan(jd.cohens_kappa(["x", "x"], ["x", "x"]))  # undefined, not 1
    assert math.isnan(jd.cohens_kappa([], []))
    # three classes, hand computed: po = 5/6; pa = (2,2,2)/6, pb = (3,1,2)/6; pe = (6+2+4)/36 = 1/3; kappa = .75
    j = ["c", "c", "p", "p", "i", "i"]
    h = ["c", "c", "p", "c", "i", "i"]
    assert jd.cohens_kappa(j, h) == pytest.approx(0.75)
    with pytest.raises(ValueError):
        jd.cohens_kappa(["a"], ["a", "b"])
    lo, hi = jd.kappa_bootstrap_ci(a, b, n_resamples=500)
    assert lo < 0.4 < hi


# ------------------------------------------------------------------ CLI: refusal, fake stamp, judge flow

@pytest.fixture
def mini(tmp_path):
    ds = tmp_path / "ds"
    ds.mkdir()
    rows, rankings = [], []
    cats = ["single_fact", "numeric_reasoning", "unanswerable"]
    n = 0
    for split in ("dev", "validation", "test"):
        for cat in cats:
            for _ in range(4):
                n += 1
                ua = cat == "unanswerable"
                text = f"Fact number {n} is {n * 11} units."
                rows.append({"id": f"c-{n:02d}", "split": split, "category": cat, "tags": [], "question": f"What is fact {n}?",
                             "answerable": not ua, "reference_answer": "none" if ua else f"Fact {n} is {n * 11} units.",
                             "must_include": [] if ua else [str(n * 11)],
                             "evidence": [] if ua else [{"doc": "d.md", "version": 1, "quote": text, "page": None, "grade": 2}],
                             "forbidden": [], "reviewed": False, "origin": NEW})
                rankings.append({"case_id": f"c-{n:02d}", "ranking": [] if ua else [{"doc": "d.md", "text": text, "score": 0.9}]})
    (ds / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (ds / "meta.json").write_text(json.dumps({"version": "mini-v0", "review_status": "NOT YET HUMAN-REVIEWED"}), encoding="utf-8")
    rk = tmp_path / "rankings.jsonl"
    rk.write_text("".join(json.dumps(r) + "\n" for r in rankings), encoding="utf-8")
    return ds, rk, tmp_path


def run_cli(ds, rk, out, *extra):
    return main(["answer", "--dataset", str(ds), "--rankings", str(rk), "--profile", "unit", "--generator", "fake",
                 "--verifier", "fake", "--out", str(out), *extra])


def test_test_split_refused_without_milestone_report(mini, capsys):
    ds, rk, tmp = mini
    assert run_cli(ds, rk, tmp / "out", "--split", "test") == 2
    assert "INV-12" in capsys.readouterr().err
    assert not (tmp / "out").exists()  # nothing was generated or written
    # A1: a milestone report additionally needs every case of the split to be human-reviewed
    assert run_cli(ds, rk, tmp / "out", "--split", "test", "--milestone-report") == 2
    assert "not human-reviewed" in capsys.readouterr().err
    assert not (tmp / "out").exists()
    rows = [json.loads(l) for l in (ds / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    (ds / "cases.jsonl").write_text("".join(json.dumps({**r, "reviewed": True}) + "\n" for r in rows), encoding="utf-8")
    assert run_cli(ds, rk, tmp / "out", "--split", "test", "--milestone-report") == 0


def test_fake_run_is_stamped_and_not_a_measurement(mini, capsys):
    ds, rk, tmp = mini
    assert run_cli(ds, rk, tmp / "out", "--split", "dev") == 0
    (run_dir,) = list((tmp / "out").iterdir())
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["measurement"] is False and run["stamp"] == "FAKE - NOT A MEASUREMENT"
    assert set(run["fake_components"]) == {"generator", "verifier"}
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# FAKE - NOT A MEASUREMENT")
    assert "human labels pending" in report
    # INV-11 record
    assert run["dataset_version"] == "mini-v0" and len(run["dataset_sha256"]) == 64 and len(run["config_hash"]) == 64
    assert run["model_ids"] == {"generator": "fake-generator", "verifier": "fake-verifier"}
    assert run["prompt_versions"]["chat"].startswith("chat-evidence-") and run["prompt_versions"]["claim_verify"]
    assert run["code_commit"] and run["split"] == "dev"
    rows = [json.loads(l) for l in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 12 and {r["category"] for r in rows} == {"single_fact", "numeric_reasoning", "unanswerable"}
    # the fake restates the evidence and abstains on the closed gate: every case is correct and grounded
    assert all(r["metrics"]["correct"] == 1.0 for r in rows)
    assert "FAKE - NOT A MEASUREMENT" in capsys.readouterr().out


def test_fake_run_needs_explicit_out_and_real_needs_a_key(mini, monkeypatch, capsys):
    ds, rk, tmp = mini
    monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
    assert main(["answer", "--dataset", str(ds), "--rankings", str(rk), "--split", "dev", "--generator", "fake"]) == 2
    assert "explicit --out" in capsys.readouterr().err
    assert main(["answer", "--dataset", str(ds), "--rankings", str(rk), "--split", "dev", "--out", str(tmp / "o2")]) == 2
    assert "GOOGLE_GENERATIVE_AI_API_KEY" in capsys.readouterr().err
    assert not (tmp / "o2").exists()


def test_missing_rankings_are_an_error(mini, capsys):
    ds, rk, tmp = mini
    rk.write_text("", encoding="utf-8")
    assert run_cli(ds, rk, tmp / "out", "--split", "dev") == 2
    assert "missing" in capsys.readouterr().err


def sheet_flow(mini, judge="fake"):
    ds, rk, tmp = mini
    assert run_cli(ds, rk, tmp / "out", "--split", "dev") == 0
    (run_dir,) = list((tmp / "out").iterdir())
    code = main(["judge-sheet", "--run", str(run_dir), "--dataset", str(ds), "--out-dir", str(tmp / "sheet"),
                 "--judge", judge, "-n", "12"])
    return tmp / "sheet", code


def test_judge_sheet_is_blind_balanced_and_stamped_when_fake(mini):
    sheet_dir, code = sheet_flow(mini)
    assert code == 0
    import csv

    with (sheet_dir / "labelling_sheet.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 12 and all(r["human_label"] == "" for r in rows)
    assert Counter(r["category"] for r in rows) == Counter({"single_fact": 4, "numeric_reasoning": 4, "unanswerable": 4})
    raw = (sheet_dir / "labelling_sheet.csv").read_text(encoding="utf-8")
    assert "judge" not in raw.lower()  # the human never sees the judge verdict
    assert (sheet_dir / "labelling_sheet.md").read_text(encoding="utf-8").startswith("# FAKE - NOT A MEASUREMENT")
    key = json.loads((sheet_dir / "judge_key.json").read_text(encoding="utf-8"))
    assert key["judge_is_fake"] and key["answers_are_fake"] and len(key["verdicts"]) == 12


def test_agreement_pending_then_refused_for_fakes(mini, capsys):
    sheet_dir, _ = sheet_flow(mini)
    key_path = sheet_dir / "judge_key.json"
    assert main(["judge-agreement", "--key", str(key_path), "--labels", str(sheet_dir / "nope.csv")]) == 1
    out = capsys.readouterr().out
    assert "FAKE - NOT A MEASUREMENT" in out and "kappa:" not in out
    # even with a complete labels file a fake judge/answers can never publish a kappa
    import csv

    with (sheet_dir / "labelling_sheet.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["human_label"] = "correct"
    with (sheet_dir / "human_labels.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=jd.SHEET_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    assert main(["judge-agreement", "--key", str(key_path), "--labels", str(sheet_dir / "human_labels.csv")]) == 1
    out = capsys.readouterr().out
    assert "refused_fake" in out and "kappa:" not in out
    assert "kappa:" not in (sheet_dir / "judge_agreement.md").read_text(encoding="utf-8")


def _real_key(verdicts):
    return {"judge_model": "stub-judge", "judge_is_fake": False, "answers_are_fake": False, "n": len(verdicts),
            "verdicts": verdicts, "labels_allowed": list(jd.LABELS)}


def write_labels(path, labels):
    path.write_text("sample_id,human_label,notes\n" + "".join(f"{s},{l},\n" for s, l in labels.items()), encoding="utf-8")


def test_agreement_report_pending_incomplete_invalid_ok(tmp_path):
    verdicts = {"S001": "correct", "S002": "correct", "S003": "incorrect", "S004": "partially_correct"}
    key = _real_key(verdicts)
    pending = jd.agreement_report(key, tmp_path / "missing.csv")
    assert pending["status"] == "pending" and pending["kappa"] is None
    assert "human labels pending" in jd.agreement_markdown(pending)
    assert jd.agreement_report(key, None)["status"] == "pending"
    p = tmp_path / "labels.csv"
    write_labels(p, {"S001": "correct", "S002": "", "S003": "incorrect", "S004": "partially_correct"})
    inc = jd.agreement_report(key, p)
    assert inc["status"] == "incomplete" and inc["kappa"] is None
    write_labels(p, {"S001": "correct", "S002": "maybe", "S003": "incorrect", "S004": "partially_correct"})
    assert jd.agreement_report(key, p)["status"] == "invalid"
    write_labels(p, {"S001": "correct", "S002": "incorrect", "S003": "incorrect", "S004": "partially_correct"})
    ok = jd.agreement_report(key, p)
    # po = 3/4; judge counts c2 i1 p1, human c1 i2 p1; pe = (2*1 + 1*2 + 1*1)/16 = 5/16; kappa = (.75-.3125)/(.6875)
    assert ok["status"] == "ok" and ok["n"] == 4
    assert ok["kappa"] == pytest.approx((0.75 - 5 / 16) / (1 - 5 / 16))
    assert ok["confusion_judge_rows_human_cols"]["correct"] == {"correct": 1, "partially_correct": 0, "incorrect": 1}
    assert "kappa: 0.636" in jd.agreement_markdown(ok)


def test_select_for_labelling_is_deterministic_and_capped():
    rows = [{"case_id": f"{cat}-{i:02d}", "category": cat} for cat in "abcde" for i in range(30)]
    a = jd.select_for_labelling(rows, 60)
    assert a == jd.select_for_labelling(list(reversed(rows)), 60)
    assert len(a) == 60 and Counter(r["category"] for r in a) == Counter({c: 12 for c in "abcde"})
    assert len(jd.select_for_labelling(rows[:7], 60)) == 7


# ------------------------------------------------------------------ real components (mock transport only)

def test_gemini_generator_key_in_header_only_and_errors_do_not_leak():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = str(request.url), request.headers.get("x-goog-api-key"), request.content
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "## Answer\nok [E1]."}]}}]})

    g = ans.GeminiGenerator(api_key="k-secret", model="m1", transport=httpx.MockTransport(handler))
    assert g.generate("sys", "q?", "ctx") == "## Answer\nok [E1]."
    assert "k-secret" not in seen["url"] and seen["key"] == "k-secret" and b"ctx" in seen["body"]
    bad = ans.GeminiGenerator(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="SECRET BODY")))
    with pytest.raises(RuntimeError) as ei:
        bad.generate("s", "q", "c")
    assert "SECRET" not in str(ei.value) and "500" in str(ei.value)
    assert not ans.GeminiGenerator(api_key="").configured


def test_gemini_judge_validates_verdict():
    ok = jd.GeminiJudge(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": '{"verdict": "correct"}'}]}}]})))
    assert ok.judge("q", "r", "a") == "correct"
    bad = jd.GeminiJudge(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": '{"verdict": "great"}'}]}}]})))
    with pytest.raises(RuntimeError):
        bad.judge("q", "r", "a")
    assert not jd.GeminiJudge(api_key="").configured

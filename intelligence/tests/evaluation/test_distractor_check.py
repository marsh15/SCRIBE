"""Distractor checker: detects every leak class on synthetic documents, passes a clean directory, verifies the manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scribe_eval import distractor_check as dc

REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / "benchmarks" / "datasets" / "golden-v1"
REAL = REPO / "fixtures" / "distractors"
MANIFEST = REPO / "fixtures" / "distractors.manifest.json"


def case(cid, answerable=True, evidence=(), must=(), forbidden=()):
    return {"id": cid, "split": "dev", "category": "x", "tags": [], "question": "q", "answerable": answerable,
            "reference_answer": "r", "must_include": list(must),
            "evidence": [{"doc": d, "version": 1, "quote": q, "page": None, "grade": 2} for d, q in evidence],
            "forbidden": [{"doc": d} for d in forbidden]}


@pytest.fixture
def dataset(tmp_path):
    d = tmp_path / "ds"
    d.mkdir()
    cases = [
        case("a1", evidence=[("policy.md", "Parental leave is 16 weeks at 100% of base pay.")], must=["16 weeks"]),
        case("a2", evidence=[("fleet.csv", "NW-T-0417,Reefer truck,Scania,R450,2022,D-HAM,HH-NW 417,in_service,2026-08-19,312408,9500")],
             must=["Thursdays", "30", "No"], forbidden=["old-policy.pdf"]),
        case("u1", answerable=False, must=["SHOULD-NOT-BE-ENFORCED"]),
    ]
    (d / "cases.jsonl").write_text("\n".join(json.dumps(c) for c in cases) + "\n")
    return d


@pytest.fixture
def gold(dataset):
    return dc.Gold.load(dataset)


def write(d: Path, name: str, text: str) -> Path:
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


def rules(vs):
    return sorted({v.rule for v in vs})


CLEAN = ("Cafeteria menu planning policy. The canteen supervisor publishes the weekly menu on Friday. "
         "A refund of 12 euros applies to lost meal tokens. Requests are answered within 4 working days.")


def test_clean_directory_passes(tmp_path, gold):
    d = tmp_path / "dist"
    write(d, "canteen.md", CLEAN)
    write(d, "grid.csv", "a,b\n1,2\n")
    assert dc.check_directory(d, gold) == []


def test_detects_quote_leak_with_whitespace_and_case_variation(tmp_path, gold):
    d = tmp_path / "dist"
    write(d, "x.md", "Intro.\n\nPARENTAL LEAVE is 16 weeks\nat 100% of base   pay.\n")
    vs = dc.check_directory(d, gold)
    assert "a" in rules(vs)
    assert any("a1:policy.md" in v.detail for v in vs)


def test_detects_must_include_leak_whole_word_only(tmp_path, gold):
    d = tmp_path / "dist"
    write(d, "leak.txt", "The lease allows 16 weeks of notice.")
    write(d, "ok.txt", "The lease allows 116 weeks or 16.5 weeks of notice.")  # 116 and 16.5 are not '16 weeks'
    write(d, "thursday.txt", "Meetings are held on Thursdays.")
    vs = dc.check_directory(d, gold)
    assert {v.file for v in vs if v.rule == "b"} == {"leak.txt", "thursday.txt"}


def test_bare_numbers_generic_words_and_unanswerable_must_include_are_not_enforced(tmp_path, gold):
    d = tmp_path / "dist"
    write(d, "ok.txt", "No, there are 30 items and 12 rows. SHOULD-NOT-BE-ENFORCED is only in an unanswerable case.")
    assert dc.check_directory(d, gold) == []
    assert ("a2", "30") not in gold.must_include and all(m[1] != "no" for m in gold.must_include)


def test_detects_csv_ordered_cells_leak(tmp_path, gold):
    d = tmp_path / "dist"
    # same cells, different delimiter and spacing: legacy renders a CSV row as space-joined cells
    write(d, "legacy.txt", "NW-T-0417 Reefer truck Scania R450 2022 D-HAM HH-NW 417 in_service 2026-08-19 312408 9500")
    vs = dc.check_directory(d, gold)
    assert "a" in rules(vs) and any("CSV ordered-cells" in v.detail for v in vs)
    # cells present but out of order: only the identifier rule fires
    write(d, "legacy.txt", "9500 312408 2026-08-19 in_service HH-NW 417 D-HAM 2022 R450 Scania Reefer truck NW-T-0417")
    vs = dc.check_directory(d, gold)
    assert not any("CSV ordered-cells" in v.detail for v in vs)
    assert "c" in rules(vs)


def test_detects_forbidden_identifier_exact_but_not_neighbours(tmp_path, gold):
    d = tmp_path / "dist"
    write(d, "bad.md", "Fleet unit NW-T-0417 is parked.")
    write(d, "near.md", "Fleet unit NW-T-0418 and D-HAMBURG and NW-T-04170 are different.")
    vs = dc.check_directory(d, gold)
    assert {v.file for v in vs if v.rule == "c"} == {"bad.md"}


@pytest.mark.parametrize("word", ["stock levels", "pension plan", "tachograph", "whistleblower", "Hybrid desks", "sustainability"])
def test_detects_absent_topic_keywords(tmp_path, gold, word):
    d = tmp_path / "dist"
    write(d, "t.txt", f"A note about {word} at the depot.")
    assert "c" in rules(dc.check_directory(d, gold))


def test_detects_name_collision_case_insensitive(tmp_path, gold):
    d = tmp_path / "dist"
    write(d, "Old-Policy.pdf.txt", CLEAN)  # different file: no collision
    write(d, "policy.md", CLEAN)
    write(d, "FACTS.md", CLEAN)
    write(d, "something.truth.txt", CLEAN)
    vs = dc.check_directory(d, gold)
    assert {v.file for v in vs if v.rule == "d"} == {"policy.md", "FACTS.md", "something.truth.txt"}


def make_manifest(d: Path, tmp: Path) -> Path:
    files = [{"name": p.name, "size": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "format": p.suffix[1:]}
             for p in sorted(d.iterdir())]
    m = tmp / "manifest.json"
    m.write_text(json.dumps({"files": files}))
    return m


def test_manifest_verification(tmp_path):
    d = tmp_path / "dist"
    write(d, "a.md", CLEAN)
    write(d, "b.txt", "Second document.")
    m = make_manifest(d, tmp_path)
    assert dc.verify_manifest(d, m) == []
    (d / "b.txt").write_text("Second document!")  # changed bytes
    (d / "c.txt").write_text("extra")  # not listed
    (d / "a.md").unlink()  # missing
    vs = dc.verify_manifest(d, m)
    assert {(v.file, v.rule) for v in vs} == {("b.txt", "m"), ("c.txt", "m"), ("a.md", "m")}


def test_cli_exit_codes(tmp_path, dataset, capsys):
    d = tmp_path / "dist"
    write(d, "canteen.md", CLEAN)
    argv = ["--distractors", str(d), "--dataset", str(dataset), "--gold-corpus", "", "--no-chunks"]
    assert dc.main(argv) == 0
    out = capsys.readouterr().out
    assert "OK:" in out and "1 documents" in out
    write(d, "leak.md", "Parental leave is 16 weeks at 100% of base pay.")
    assert dc.main(argv) == 1
    assert "[a] leak.md" in capsys.readouterr().err
    assert dc.main(["--distractors", str(tmp_path / "missing"), "--dataset", str(dataset)]) == 2


def test_cli_summary_counts_chunks_by_format(tmp_path, dataset, capsys):
    d = tmp_path / "dist"
    write(d, "big.txt", ("Paragraph about parking permits. " * 40 + "\n\n") * 12)  # ~16k chars -> several legacy chunks
    write(d, "grid.csv", "id,qty\n" + "\n".join(f"P{i},{i}" for i in range(50)))
    dc.summarize(d, sample=4)
    out = capsys.readouterr().out
    assert "TOTAL legacy chunks" in out and "ESTIMATE" in out
    assert "txt" in out and "csv" in out


def test_fallback_cell_helpers_match_metrics():
    """The stdlib fallbacks (used by the --no-project generator) must behave like scribe_eval.metrics."""
    import csv as _csv

    from scribe_eval import metrics
    from scribe_eval.normalize import normalize

    def fb_cells(raw_line):
        best = []
        for delim in (",", ";", "\t", "|"):
            try:
                row = next(_csv.reader([raw_line], delimiter=delim))
            except (_csv.Error, StopIteration):
                continue
            if len(row) > len(best):
                best = row
        return [c for c in (normalize(c) for c in best) if c]

    for line in ['a,b,,c', 'x;y;"z;w"', 'NW-PAL-120;EUR pallet;each;14,90;50;2026-01-01', 'D-HAM,Hamburg,DE,"Hansestrasse 41, 20457 Hamburg"']:
        assert fb_cells(line) == metrics.csv_cells(line)


# ---------------------------------------------------------------- the real corpus
@pytest.mark.skipif(not REAL.is_dir() or not any(REAL.iterdir()), reason="fixtures/distractors not generated (run fixtures/build_distractors.py)")
def test_real_distractors_pass_checker():
    gold = dc.Gold.load(DATASET, REPO / "fixtures" / "corpus")
    assert gold.must_include and gold.quotes and len(gold.absent) >= 15
    violations = dc.check_directory(REAL, gold)
    assert violations == [], "\n".join(str(v) for v in violations[:20])
    if MANIFEST.is_file():
        assert dc.verify_manifest(REAL, MANIFEST) == []


@pytest.mark.skipif(not REAL.is_dir() or not any(REAL.iterdir()), reason="fixtures/distractors not generated")
def test_real_distractors_have_expected_shape():
    docs = dc.list_documents(REAL)
    by = {}
    for p in docs:
        by[p.suffix] = by.get(p.suffix, 0) + 1
    assert len(docs) >= 290
    assert by.get(".pdf", 0) >= 55 and by.get(".docx", 0) >= 55 and by.get(".csv", 0) >= 40
    assert sum(p.stat().st_size for p in docs) < 12 * 1024 * 1024


def test_gold_documents_themselves_are_caught_by_the_checker(tmp_path):
    """Sanity check of the detector on the real labels: copying a gold text document into the distractor dir must fail."""
    corpus = REPO / "fixtures" / "corpus"
    if not (corpus / "runbook-deploy.md").is_file():
        pytest.skip("gold corpus not present")
    d = tmp_path / "dist"
    d.mkdir()
    (d / "renamed-runbook.md").write_text((corpus / "runbook-deploy.md").read_text(encoding="utf-8"), encoding="utf-8")
    (d / "renamed-fleet.csv").write_text((corpus / "fleet-inventory.csv").read_text(encoding="utf-8"), encoding="utf-8")
    vs = dc.check_directory(d, dc.Gold.load(DATASET, corpus))
    assert {v.file for v in vs if v.rule == "a"} == {"renamed-runbook.md", "renamed-fleet.csv"}

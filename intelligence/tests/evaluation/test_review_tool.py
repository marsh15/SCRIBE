"""A1: the local review tool. Everything runs on a tmp copy of a few real golden cases and the real fixtures/corpus;
the real benchmarks/datasets/golden-v1/cases.jsonl is only read, never written."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scribe_eval.cli import main
from scribe_eval.dataset import Case, load_cases
from scribe_eval.review import store as store_mod
from scribe_eval.review.app import create_app
from scribe_eval.review.kinds import ADAPTERS, ChangeItem, FieldItem
from scribe_eval.review.require import refusal_message, unreviewed_ids
from scribe_eval.review.server import check_host, serve
from scribe_eval.review.source import Corpus, locate
from scribe_eval.review.store import ids_from_markdown, ids_from_order_file

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "benchmarks" / "datasets" / "golden-v1"
CORPUS = REPO / "fixtures" / "corpus"
SAMPLE = ["sf-01", "sf-02", "sf-03", "tb-05", "dm-01", "ua-01", "cd-13"]  # pdf, txt, md, docx, scanned pdf, unanswerable, T28


def sample_lines() -> list[str]:
    by_id = {json.loads(l)["id"]: l for l in (GOLDEN / "cases.jsonl").read_text(encoding="utf-8").split("\n") if l}
    return [by_id[i] for i in SAMPLE]


@pytest.fixture
def ds(tmp_path):
    d = tmp_path / "ds"
    d.mkdir()
    (d / "cases.jsonl").write_bytes(("\n".join(sample_lines()) + "\n").encode("utf-8"))
    return d


@pytest.fixture
def client(ds):
    app = create_app(ds / "cases.jsonl", "case", CORPUS, "tester", order=[])
    return TestClient(app, base_url="http://127.0.0.1")


def raw_rows(ds: Path) -> list[dict]:
    return [json.loads(l) for l in (ds / "cases.jsonl").read_text(encoding="utf-8").split("\n") if l]


def item_bytes(ds: Path) -> bytes:
    return (ds / "cases.jsonl").read_bytes()


# ------------------------------------------------------------------ schema

def test_case_schema_accepts_review_fields_and_old_files_parse_unchanged():
    assert len(load_cases(GOLDEN / "cases.jsonl")) == 200
    row = json.loads(sample_lines()[0])
    c = Case.model_validate({**row, "reviewed": True, "reviewed_by": "me", "reviewed_at": "2026-01-01T00:00:00Z", "review_note": "ok"})
    assert c.reviewed_by == "me" and c.review_note == "ok"
    with pytest.raises(Exception):
        Case.model_validate({**row, "surprise": 1})  # still extra="forbid"


def test_unreviewed_ids_treats_absent_none_and_false_as_unreviewed():
    rows = [{"id": "a", "reviewed": True}, {"id": "b"}, {"id": "c", "reviewed": None}, {"id": "d", "reviewed": False}]
    assert unreviewed_ids(rows) == ["b", "c", "d"]
    assert "3 of 4" in refusal_message(["b", "c", "d"], 4, "do x")


# ------------------------------------------------------------------ decisions

def test_untouched_round_trip_is_byte_identical(ds):
    before = item_bytes(ds)
    # the on-disk style equals the writer's style, so an identity rewrite of a line changes no byte
    assert all(json.dumps(json.loads(l), ensure_ascii=False) == l for l in before.decode().split("\n") if l)
    st = create_app(ds / "cases.jsonl", "case", CORPUS, "tester").state.store
    st._replace("sf-02", lambda old: dict(old))
    assert item_bytes(ds) == before


def test_accept_marks_only_that_item_and_leaves_other_lines_byte_identical(ds, client):
    before = item_bytes(ds).decode().split("\n")
    r = client.post("/api/items/sf-02/accept", json={"note": "checked against the postmortem"})
    assert r.status_code == 200, r.text
    after = item_bytes(ds).decode().split("\n")
    assert [i for i, (a, b) in enumerate(zip(before, after)) if a != b] == [1]
    row = json.loads(after[1])
    assert row["reviewed"] is True and row["reviewed_by"] == "tester" and row["review_note"] == "checked against the postmortem"
    assert row["reviewed_at"].endswith("Z") and len(row["reviewed_at"]) == 20
    assert list(row)[: len(json.loads(before[1]))] == list(json.loads(before[1]))  # existing key order kept, new keys appended
    assert r.json()["next"] == "sf-03" and r.json()["state"]["reviewed"] == 1


def test_accept_on_a_t28_case_flips_reviewed_in_place(ds, client):
    before = json.loads(sample_lines()[-1])
    assert before["reviewed"] is False
    assert client.post("/api/items/cd-13/accept", json={}).status_code == 200
    row = raw_rows(ds)[-1]
    assert row["reviewed"] is True and "review_note" not in row
    assert list(row)[: len(before)] == list(before)


def test_edit_applies_form_values_validates_and_marks_reviewed(ds, client):
    ev = json.loads(sample_lines()[0])["evidence"][0]
    values = {"question": "When do I collect my badge?", "tags": ["pdf", "edited"], "must_include": ["09:00", "reception"],
              "evidence": [{"doc": ev["doc"], "version": "1", "quote": ev["quote"], "page": "2", "grade": "2"}],
              "forbidden": ["hr-leave-policy-2024.pdf"]}
    r = client.post("/api/items/sf-01/edit", json={"values": values, "note": "reworded"})
    assert r.status_code == 200, r.text
    row = raw_rows(ds)[0]
    assert row["question"] == "When do I collect my badge?" and row["tags"] == ["pdf", "edited"]
    assert row["must_include"] == ["09:00", "reception"] and row["forbidden"] == [{"doc": "hr-leave-policy-2024.pdf"}]
    assert row["evidence"][0]["version"] == 1 and row["evidence"][0]["page"] == 2 and row["evidence"][0]["grade"] == 2
    assert row["reviewed"] is True and row["review_note"] == "reworded"
    Case.model_validate(row)


@pytest.mark.parametrize("values", [
    {"evidence": [{"doc": "onboarding-handbook.pdf", "version": 1, "quote": "this sentence is not in the handbook", "page": 2, "grade": 2}]},
    {"evidence": [{"doc": "onboarding-handbook.pdf", "version": 1, "quote": "report to reception", "page": 2, "grade": 3}]},
    {"evidence": [{"doc": "no-such-doc.pdf", "version": 1, "quote": "x", "page": 1, "grade": 2}]},
    {"evidence": [{"doc": "onboarding-handbook.pdf", "version": 1, "quote": "x" * 201, "page": 2, "grade": 2}]},
    {"answerable": False},  # unanswerable but evidence left in place
    {"answerable": "no"},
    {"evidence": [{"doc": "onboarding-handbook.pdf", "version": 1, "quote": "report to reception", "page": "two", "grade": 2}]},
])
def test_invalid_edit_writes_nothing_and_reports_the_error(ds, client, values):
    before = item_bytes(ds)
    r = client.post("/api/items/sf-01/edit", json={"values": values})
    assert r.status_code == 422 and r.json()["errors"]
    assert item_bytes(ds) == before
    assert not (ds / "dropped.jsonl").exists()


def test_drop_moves_the_item_to_dropped_jsonl_with_who_when_why(ds, client):
    before = item_bytes(ds).decode().split("\n")
    r = client.post("/api/items/sf-03/drop", json={"reason": "  quote is\nambiguous  "})
    assert r.status_code == 200, r.text
    after = item_bytes(ds).decode().split("\n")
    assert after == before[:2] + before[3:]  # exactly that line removed, nothing else touched
    (rec,) = [json.loads(l) for l in (ds / "dropped.jsonl").read_text(encoding="utf-8").split("\n") if l]
    orig = json.loads(before[2])
    assert {k: rec[k] for k in orig} == orig and rec["dropped_by"] == "tester" and rec["drop_reason"] == "quote is ambiguous"
    assert rec["dropped_at"].endswith("Z")
    st = r.json()["state"]
    assert st["total"] == 6 and st["dropped"] == 1 and r.json()["next"] == "tb-05"
    assert client.get("/api/items/sf-03").status_code == 404
    # a second drop appends to the same file
    assert client.post("/api/items/ua-01/drop", json={"reason": "not useful"}).status_code == 200
    assert len([l for l in (ds / "dropped.jsonl").read_text().split("\n") if l]) == 2


def test_drop_needs_a_reason_and_writes_nothing_without_one(ds, client):
    before = item_bytes(ds)
    assert client.post("/api/items/sf-03/drop", json={"reason": "   "}).status_code == 422
    assert client.post("/api/items/sf-03/drop", json={}).status_code == 422
    assert item_bytes(ds) == before and not (ds / "dropped.jsonl").exists()


def test_a_failure_during_the_write_leaves_the_original_intact(ds, monkeypatch):
    app = create_app(ds / "cases.jsonl", "case", CORPUS, "tester")
    c = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    before = item_bytes(ds)

    def boom(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(store_mod.os, "replace", boom)
    assert c.post("/api/items/sf-01/accept", json={}).status_code == 500
    assert item_bytes(ds) == before
    assert sorted(p.name for p in ds.iterdir()) == ["cases.jsonl"]  # no temp file left behind
    # and a failure in the temp write itself (before the rename) behaves the same
    monkeypatch.undo()
    monkeypatch.setattr(store_mod.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync failed")))
    assert c.post("/api/items/sf-01/drop", json={"reason": "r"}).status_code == 500
    assert item_bytes(ds) == before and not (ds / "dropped.jsonl").exists()
    assert sorted(p.name for p in ds.iterdir()) == ["cases.jsonl"]


def test_atomic_write_uses_a_same_directory_temp_file_and_keeps_permissions(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_text("old")
    os.chmod(p, 0o640)
    store_mod.atomic_write_text(p, "new")
    assert p.read_text() == "new" and (p.stat().st_mode & 0o777) == 0o640
    assert [x.name for x in tmp_path.iterdir()] == ["f.jsonl"]


# ------------------------------------------------------------------ order and resume

def test_resume_starts_at_the_first_item_that_is_not_reviewed(ds):
    rows = raw_rows(ds)
    for r in rows[:3]:
        r["reviewed"] = True
    (ds / "cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    c = TestClient(create_app(ds / "cases.jsonl", "case", CORPUS, "t", order=[]), base_url="http://127.0.0.1")
    s = c.get("/api/state").json()
    assert s["current"] == "tb-05" and s["reviewed"] == 3 and s["remaining"] == 4 and s["total"] == 7 and s["dropped"] == 0


def test_resume_follows_the_requested_order_and_counts_dropped(ds):
    c = TestClient(create_app(ds / "cases.jsonl", "case", CORPUS, "t", order=["ua-01", "nope", "sf-02"]), base_url="http://127.0.0.1")
    assert [x["id"] for x in c.get("/api/state").json()["sequence"]][:4] == ["ua-01", "sf-02", "sf-01", "sf-03"]
    assert c.get("/api/state").json()["current"] == "ua-01"
    c.post("/api/items/ua-01/accept", json={})
    assert c.get("/api/state").json()["current"] == "sf-02"
    c.post("/api/items/sf-02/drop", json={"reason": "x"})
    s = c.get("/api/state").json()
    assert s["current"] == "sf-01" and s["dropped"] == 1 and s["reviewed"] == 1 and s["remaining"] == 5


def test_next_skips_without_deciding_and_wraps(client, ds):
    before = item_bytes(ds)
    assert client.get("/api/nav", params={"frm": "sf-01"}).json()["id"] == "sf-02"
    assert client.get("/api/nav", params={"frm": "cd-13"}).json()["id"] == "sf-01"
    assert client.get("/api/nav", params={"frm": "sf-02", "direction": "prev", "pending": False}).json()["id"] == "sf-01"
    assert item_bytes(ds) == before


def test_default_order_for_golden_v1_is_review_first_then_t28_then_the_rest():
    app = create_app(GOLDEN / "cases.jsonl", "case", CORPUS, "t")
    seq = [x["id"] for x in app.state.store.state()["sequence"]]
    assert len(seq) == 200 and len(set(seq)) == 200
    assert seq[:3] == ["ua-10", "ua-11", "ua-12"]
    first_t28 = seq.index("tb-13")
    assert seq.index("tb-16") == first_t28 + 3 and seq.index("tb-14") == first_t28 + 1  # the tb-13..tb-16 range is expanded
    t28 = {c.id for c in load_cases(GOLDEN / "cases.jsonl") if c.origin == "machine-drafted-t28"}
    named = [i for i in seq if i in t28]
    pos = [seq.index(i) for i in named]
    assert pos == sorted(pos)


def test_markdown_and_order_file_parsing(tmp_path):
    md = tmp_path / "x.md"
    md.write_text("# t\n\nsee zz-99 in prose\n\n| Case | Concern |\n|---|---|\n| ua-11, ua-12 | mentions dm-01 |\n"
                  "| tb-13..tb-15, tb-18 | r |\n| `cd-04` | q |\n")
    assert ids_from_markdown(md) == ["ua-11", "ua-12", "tb-13", "tb-14", "tb-15", "tb-18", "cd-04"]
    of = tmp_path / "order.txt"
    of.write_text("# first\nb-1, a-2\n\nc-3 d-4\n")
    assert ids_from_order_file(of) == ["b-1", "a-2", "c-3", "d-4"]


# ------------------------------------------------------------------ source pages and highlighting

def test_quote_is_highlighted_on_the_right_pdf_page_and_missing_on_a_wrong_page():
    quote = json.loads(sample_lines()[0])["evidence"][0]["quote"]
    corpus = Corpus(CORPUS)
    ok = corpus.view("onboarding-handbook.pdf", 2, quote)
    assert ok.found and ok.warning is None and ok.mode == "pdf" and ok.image_url
    hits = [s.text for s in ok.segments if s.hit]
    assert len(hits) == 1 and " ".join(hits[0].split()).lower() == " ".join(quote.split()).lower()
    wrong = corpus.view("onboarding-handbook.pdf", 3, quote)
    assert not wrong.found and wrong.warning == "QUOTE NOT FOUND ON THIS PAGE"
    assert not any(s.hit for s in wrong.segments)
    assert any("page(s) 2" in n for n in wrong.notes)
    assert corpus.view("onboarding-handbook.pdf", 99, quote).warning.startswith("PAGE 99 DOES NOT EXIST")
    nopage = corpus.view("onboarding-handbook.pdf", None, quote)
    assert nopage.found and nopage.page == 2


def test_highlighting_ignores_case_whitespace_and_curly_quotes():
    text = "Intro.\nOn your   FIRST day,\n report to “reception”  at 09:00.  Bye"
    a, b = locate(text, "on your first day, report to \"reception\" at 09:00.")
    assert text[a:b] == "On your   FIRST day,\n report to “reception”  at 09:00."
    assert locate(text, "something else") is None and locate(text, "   ") is None


def test_the_api_shows_the_wrong_page_warning_for_a_case_with_a_wrong_page(ds, client):
    rows = raw_rows(ds)
    rows[0]["evidence"][0]["page"] = 3
    (ds / "cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    d = client.get("/api/items/sf-01").json()
    (src,) = d["sources"]
    assert src["warning"] == "QUOTE NOT FOUND ON THIS PAGE" and src["found"] is False and src["page"] == 3
    good = client.get("/api/items/sf-02").json()["sources"][0]
    assert good["found"] and good["mode"] == "text" and any(s["hit"] for s in good["segments"]) and good["warning"] is None


def test_page_images_render_for_pdfs_and_a_scanned_pdf_shows_its_truth_text(client):
    png = client.get("/api/page.png", params={"doc": "onboarding-handbook.pdf", "page": 2})
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n" and png.headers["content-type"] == "image/png"
    assert client.get("/api/page.png", params={"doc": "onboarding-handbook.pdf", "page": 99}).status_code == 404
    assert client.get("/api/page.png", params={"doc": "runbook-deploy.md", "page": 1}).status_code == 404
    assert client.get("/api/page.png", params={"doc": "../pyproject.toml", "page": 1}).status_code == 404
    scan = client.get("/api/items/dm-01").json()["sources"][0]
    assert scan["mode"] == "scan" and scan["image_url"] and scan["found"] and any(s["hit"] for s in scan["segments"])
    assert client.get(scan["image_url"]).status_code == 200
    docx = client.get("/api/items/tb-05").json()["sources"][0]
    assert docx["mode"] == "text" and docx["found"]
    assert client.get("/api/items/ua-01").json()["sources"] == []


def test_index_page_is_served_and_has_the_keys(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    for needle in ('id="b-accept"', 'id="b-edit"', 'id="b-drop"', 'id="b-next"', "dropped.jsonl", "tester"):
        assert needle in r.text


# ------------------------------------------------------------------ local only

def test_binding_to_anything_but_127_0_0_1_is_refused(ds):
    app = create_app(ds / "cases.jsonl", "case", CORPUS, "t")
    for host in ("0.0.0.0", "::", "192.168.1.5", "localhost", ""):
        with pytest.raises(ValueError, match="127.0.0.1"):
            check_host(host)
        with pytest.raises(ValueError, match="127.0.0.1"):
            serve(app, port=0, host=host)
    check_host("127.0.0.1")


def test_requests_with_a_foreign_host_or_origin_are_refused(ds):
    app = create_app(ds / "cases.jsonl", "case", CORPUS, "t")
    assert TestClient(app, base_url="http://evil.example").get("/api/state").status_code == 403
    ok = TestClient(app, base_url="http://127.0.0.1:8765")
    assert ok.get("/api/state").status_code == 200
    assert ok.post("/api/items/sf-01/accept", json={}, headers={"origin": "http://evil.example"}).status_code == 403
    assert raw_rows(ds)[0].get("reviewed") is None


# ------------------------------------------------------------------ other kinds

def test_field_and_change_models_and_json_editing(tmp_path):
    f = {"id": "f-1", "doc": "onboarding-handbook.pdf", "page": 2, "field_id": "badge_time", "kind": "text", "expected": "09:00",
         "quote": "report to reception at 09:00"}
    ch = {"id": "c-1", "pair_id": "leave", "old_doc": "hr-leave-policy-2024.pdf", "new_doc": "hr-leave-policy-2026.pdf",
          "section": "carry-over", "change_type": "changed", "old_value": "5", "new_value": "10", "old_page": 1, "new_page": 1}
    FieldItem.model_validate(f)
    ChangeItem.model_validate(ch)
    for model, bad in ((FieldItem, {**f, "kind": "image"}), (ChangeItem, {**ch, "change_type": "renamed"}), (FieldItem, {**f, "x": 1})):
        with pytest.raises(Exception):
            model.model_validate(bad)
    items = tmp_path / "fields.jsonl"
    items.write_text(json.dumps(f) + "\n" + json.dumps({**f, "id": "f-2", "page": 3}) + "\n")
    c = TestClient(create_app(items, "field", CORPUS, "t"), base_url="http://127.0.0.1")
    d = c.get("/api/items/f-1").json()
    assert d["editor"]["mode"] == "json" and d["sources"][0]["found"]
    assert c.get("/api/items/f-2").json()["sources"][0]["warning"] == "QUOTE NOT FOUND ON THIS PAGE"
    before = items.read_bytes()
    for bad in ("{not json", json.dumps({**f, "kind": "image"}), json.dumps({**f, "id": "other"}), json.dumps({**f, "doc": "gone.pdf"}), "[1]"):
        r = c.post("/api/items/f-1/edit", json={"values": {"__json__": bad}})
        assert r.status_code == 422 and r.json()["errors"], bad
        assert items.read_bytes() == before
    r = c.post("/api/items/f-1/edit", json={"values": {"__json__": json.dumps({**f, "expected": "9:00"})}})
    assert r.status_code == 200
    row = [json.loads(l) for l in items.read_text().splitlines()][0]
    assert row["expected"] == "9:00" and row["reviewed"] is True and row["reviewed_by"] == "t"

    chg = tmp_path / "changes.jsonl"
    chg.write_text(json.dumps(ch) + "\n")
    cc = TestClient(create_app(chg, "change", CORPUS, "t"), base_url="http://127.0.0.1")
    srcs = cc.get("/api/items/c-1").json()["sources"]
    assert [s["label"] for s in srcs] == ["old", "new"] and srcs[0]["doc"] == "hr-leave-policy-2024.pdf"
    assert cc.post("/api/items/c-1/accept", json={}).status_code == 200
    assert json.loads(chg.read_text())["reviewed"] is True
    assert set(ADAPTERS) == {"case", "field", "change"}


def test_review_command_validates_its_inputs(tmp_path, capsys):
    assert main(["review", "--items", str(tmp_path / "missing.jsonl"), "--corpus", str(CORPUS)]) == 2
    assert "not found" in capsys.readouterr().err
    assert main(["review", "--items", str(GOLDEN / "cases.jsonl"), "--corpus", str(tmp_path / "nope")]) == 2


# ------------------------------------------------------------------ validate --require-reviewed

def test_validate_require_reviewed_fails_and_lists_every_unreviewed_case(capsys):
    assert main(["validate", str(GOLDEN), "--corpus", str(CORPUS)]) == 0
    capsys.readouterr()
    assert main(["validate", str(GOLDEN), "--corpus", str(CORPUS), "--require-reviewed"]) == 1
    err = capsys.readouterr().err
    assert "200 of 200 case(s) are not human-reviewed" in err
    assert err.count("not reviewed") == 200 and "[sf-01] not reviewed" in err and "[ua-20] not reviewed" in err


def test_validate_require_reviewed_passes_when_every_case_is_reviewed(tmp_path, capsys):
    d = tmp_path / "g"
    d.mkdir()
    (d / "meta.json").write_bytes((GOLDEN / "meta.json").read_bytes())
    rows = [json.loads(l) for l in (GOLDEN / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l]
    rows[0]["reviewed"] = None  # None counts as unreviewed
    for r in rows[1:]:
        r["reviewed"] = True
    (d / "cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert main(["validate", str(d), "--corpus", str(CORPUS), "--require-reviewed"]) == 1
    assert "1 of 200" in capsys.readouterr().err
    rows[0]["reviewed"] = True
    (d / "cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert main(["validate", str(d), "--corpus", str(CORPUS), "--require-reviewed"]) == 0


# ------------------------------------------------------------------ refusals

def mini_dataset(tmp_path: Path, reviewed: bool | None) -> tuple[Path, Path]:
    d = tmp_path / "mini"
    d.mkdir()
    rows = []
    for i, split in enumerate(["dev", "dev", "validation", "test", "test"]):
        r = {"id": f"m-{i}", "split": split, "category": "unanswerable", "tags": [], "question": "q?", "answerable": False,
             "reference_answer": "none", "must_include": [], "evidence": [], "forbidden": []}
        if reviewed is not None:
            r["reviewed"] = reviewed
        rows.append(r)
    rows[3]["reviewed"] = True  # test split: one reviewed, one not (when reviewed is not True)
    (d / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"version": "mini"}), encoding="utf-8")
    rk = tmp_path / "rk.jsonl"
    rk.write_text("".join(json.dumps({"case_id": r["id"], "ranking": []}) + "\n" for r in rows), encoding="utf-8")
    return d, rk


@pytest.mark.parametrize("cmd", ["score", "answer"])
def test_milestone_report_is_refused_while_the_split_has_unreviewed_cases(tmp_path, capsys, cmd):
    d, rk = mini_dataset(tmp_path, reviewed=None)
    extra = ["--generator", "fake", "--verifier", "fake", "--out", str(tmp_path / "o")] if cmd == "answer" else ["--out", str(tmp_path / "o")]
    rc = main([cmd, "--rankings", str(rk), "--dataset", str(d), "--split", "test", "--milestone-report", *extra])
    err = capsys.readouterr().err
    assert rc == 2 and "1 of 2 case(s) are not human-reviewed" in err and "m-4" in err
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("cmd", ["score", "answer"])
def test_milestone_report_runs_when_the_split_is_fully_reviewed(tmp_path, capsys, cmd):
    d, rk = mini_dataset(tmp_path, reviewed=None)
    rows = [json.loads(l) for l in (d / "cases.jsonl").read_text().splitlines()]
    for r in rows:
        r["reviewed"] = r["split"] == "test"  # dev/validation stay unreviewed: only the reported split matters
    (d / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    extra = ["--generator", "fake", "--verifier", "fake"] if cmd == "answer" else []
    rc = main([cmd, "--rankings", str(rk), "--dataset", str(d), "--split", "test", "--milestone-report", "--out", str(tmp_path / "o"), *extra])
    assert rc == 0, capsys.readouterr().err


def test_dev_scoring_without_milestone_report_is_not_affected_by_review_state(tmp_path, capsys):
    d, rk = mini_dataset(tmp_path, reviewed=None)
    assert main(["score", "--rankings", str(rk), "--dataset", str(d), "--split", "dev", "--out", str(tmp_path / "o")]) == 0


def write_run(tmp_path: Path, split: str) -> Path:
    r = tmp_path / "run"
    r.mkdir()
    (r / "run.json").write_text(json.dumps({"run_id": "11111111-1111-1111-1111-111111111111", "dataset_version": "mini",
                                            "split": split, "measurement": True}), encoding="utf-8")
    (r / "results.jsonl").write_text(json.dumps({"case_id": "m-0", "metrics": {}}) + "\n", encoding="utf-8")
    return r


def test_publish_run_is_refused_for_unreviewed_data_unless_seeded_or_fake(tmp_path, capsys):
    d, _ = mini_dataset(tmp_path, reviewed=None)
    run = write_run(tmp_path, "dev")
    base = ["publish-run", str(run), "--database-url", "postgresql://nobody@127.0.0.1:1/none", "--dataset", str(d)]
    assert main(base) == 2
    err = capsys.readouterr().err
    assert "2 of 2 case(s) are not human-reviewed" in err and "m-0" in err
    for flag in ("--seeded", "--mark-fake"):
        main([*base, flag])  # proceeds past the review check; fails later on the unreachable database
        assert "not human-reviewed" not in capsys.readouterr().err
    rows = [json.loads(l) for l in (d / "cases.jsonl").read_text().splitlines()]
    (d / "cases.jsonl").write_text("".join(json.dumps({**r, "reviewed": r["split"] == "dev"}) + "\n" for r in rows), encoding="utf-8")
    main(base)  # the run's split (dev) is fully reviewed now
    assert "not human-reviewed" not in capsys.readouterr().err

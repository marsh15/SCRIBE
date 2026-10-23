import importlib.util
import json
import shutil
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from scribe_eval.cache import CacheMiss, DiskCache
from scribe_eval.commands.parse_compare import _markdown, compare_fields, run
from scribe_eval.cost import SpendError, estimate_tokens, spend_total, tokens_usd, read_ledger
from scribe_eval.parsing.dataset import FieldDatasetError, load_fields, safe_doc_path
from scribe_eval.parsing.engines import (
    OUTPUT_TOKEN_ALLOWANCE, GeminiVisionEngine, PdfPlumberEngine, TesseractEngine,
)
from scribe_eval.parsing.score import match_expected, normalize_value
from scribe_ingest.ocr import OCR_PROMPT, OcrResult

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CORPUS = (PROJECT_ROOT / "fixtures" / "corpus").resolve()
PDF = CORPUS / "hr-leave-policy-2026.pdf"


def test_load_fielditem_schema_and_reject_escape(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "doc.pdf").write_bytes(b"pdf")
    fields = tmp_path / "fields.jsonl"
    fields.write_text(json.dumps({"id": "x", "doc": "doc.pdf", "field_id": "name", "kind": "text",
                                  "expected": "Ada", "reviewed": False}) + "\n")
    assert load_fields(fields, root)[0]["expected"] == "Ada"
    fields.write_text(json.dumps({"id": "x", "doc": "../secret.pdf", "field_id": "name", "kind": "text",
                                  "expected": "Ada"}) + "\n")
    with pytest.raises(FieldDatasetError, match="escapes corpus root"):
        load_fields(fields, root)
    fields.write_text(json.dumps({"id": "x", "doc": "doc.pdf", "page": 0, "field_id": "name",
                                  "kind": "text", "expected": "Ada"}) + "\n")
    with pytest.raises(FieldDatasetError, match="positive one-based"):
        load_fields(fields, root)


def test_symlink_cannot_escape_corpus(tmp_path):
    root, outside = tmp_path / "corpus", tmp_path / "outside.pdf"
    root.mkdir()
    outside.write_bytes(b"pdf")
    (root / "linked.pdf").symlink_to(outside)
    with pytest.raises(FieldDatasetError, match="escapes corpus root"):
        safe_doc_path(root, "linked.pdf")


@pytest.mark.parametrize("value,kind,expected", [
    ("1,200.00 USD", "number", (Decimal("1200"), "usd")),
    ("27 working days", "number", (Decimal("27"), "workingdays")),
    ("USD 1,200", "currency", ("USD", Decimal("1200"))),
    ("$1,200.00", "currency", ("USD", Decimal("1200"))),
    ("1 January 2026", "date", "2026-01-01"),
    ("01/02/2026", "date", "2026-01-02"),
    ("  Notice   Period ", "text", "notice period"),
    ("SEV1", "table_cell", "sev1"),
])
def test_kind_normalizers(value, kind, expected):
    assert normalize_value(value, kind) == expected


def test_exact_and_normalized_value_scoring_has_type_boundaries():
    assert match_expected("The NOTICE   period is 27 days.", "notice period", "text") == {
        "exact": False, "normalized": True}
    assert match_expected("Item 100 costs EUR 25.00 on 1 January 2026.", "10", "number") == {
        "exact": False, "normalized": False}
    assert match_expected("Item 10 costs USD 25 on 2026-01-01.", "10", "number")["normalized"]
    assert match_expected("Fee EUR 25.00", "USD 25", "currency")["normalized"] is False
    assert match_expected("Fee EUR 25.00", "€25", "currency")["normalized"]
    assert match_expected("Date 2026-01-01", "1 January 2026", "date")["normalized"]


class FakeCache:
    def __init__(self, mode="record"):
        self.mode = mode
        self.values = {}
        self.hits = 0
        self.misses = 0

    def _get(self, key):
        return (key in self.values, self.values.get(key))

    def close(self):
        pass

    async def get_or_compute(self, key, fn):
        if key in self.values:
            self.hits += 1
            return self.values[key]
        self.misses += 1
        if self.mode == "replay":
            raise CacheMiss(f"cache miss: {key}")
        value = await fn()
        self.values[key] = value
        return value


class FakeOcr:
    model = "fake-gemini"
    is_fake = True

    def __init__(self, text="fake transcription"):
        self.text = text
        self.calls = []

    async def transcribe(self, png, *, page):
        self.calls.append((png, page))
        return OcrResult(self.text, 12, 3)


def test_pdfplumber_engine_fakable_interface():
    calls = []
    engine = PdfPlumberEngine(lambda path, page: calls.append((path, page)) or "fake page")
    assert engine.extract(PDF, 2)[0] == "fake page"
    assert calls == [(PDF, 2)]


def test_tesseract_engine_uses_injected_ocr_without_optional_import():
    seen = []
    engine = TesseractEngine(lambda image: seen.append(image.size) or "fake tesseract")
    text, seconds, cost = engine.extract(PDF, 1)
    assert text == "fake tesseract" and seconds >= 0 and cost == 0
    assert seen and seen[0][0] > 0 and seen[0][1] > 0


@pytest.mark.skipif(not (shutil.which("tesseract") and importlib.util.find_spec("pytesseract")),
                    reason="optional pytesseract extra or tesseract binary is unavailable")
def test_optional_real_tesseract_engine():
    text, _, _ = TesseractEngine().extract(PDF, 1)
    assert text


@pytest.mark.asyncio
async def test_gemini_engine_fake_cache_hit_and_replay_miss(tmp_path):
    cache, client = FakeCache(), FakeOcr("Full-time employees have 27 working days.")
    engine = GeminiVisionEngine(cache=cache, client=client)
    first = await engine.extract_async(PDF, 2)
    second = await engine.extract_async(PDF, 2)
    assert first[0] == second[0] and first[2] == second[2] == 0
    assert len(client.calls) == 1 and cache.hits == 1
    replay = GeminiVisionEngine(cache=FakeCache(mode="replay"), client=FakeOcr())
    with pytest.raises(RuntimeError, match="CacheMiss"):
        await replay.extract_async(PDF, 1)


@pytest.mark.asyncio
async def test_gemini_accounting_uses_per_call_cache_result_without_global_counters():
    class BareCache:
        def __init__(self):
            self.values = {}

        async def get_or_compute(self, key, fn):
            if key not in self.values:
                self.values[key] = await fn()
            return self.values[key]

    class PricedFake(FakeOcr):
        model = "gemini-2.5-flash"

    cache, client = BareCache(), PricedFake("text from fake provider")
    engine = GeminiVisionEngine(cache=cache, client=client)
    first = await engine.extract_async(PDF, 1)
    second = await engine.extract_async(PDF, 1)
    assert len(client.calls) == 1
    assert first[2] > 0 and second[2] == 0
    assert engine.usages[0].cached is False
    assert engine.usages[1].cached is True


def test_gemini_cap_preflight_happens_before_provider_call(tmp_path):
    class PricedFake(FakeOcr):
        model = "gemini-2.5-flash"

    engine = GeminiVisionEngine(cache=FakeCache(), client=PricedFake())
    key = engine.cache_key_for(PDF, 1)
    estimated = engine.estimate_live_cost([(key, b"")])
    expected = tokens_usd("gemini-2.5-flash", estimate_tokens(OCR_PROMPT) + 8000, OUTPUT_TOKEN_ALLOWANCE)
    assert estimated == expected
    with pytest.raises(SpendError):
        engine.preflight([(PDF, 1)], spend_cap=expected / 2, ledger=tmp_path / "spend.jsonl")
    assert engine.client.calls == []
    assert not (tmp_path / "spend.jsonl").exists()


def test_report_scores_each_page_once_and_has_ci_shape():
    fields = [
        {"id": "a", "doc": "hr-leave-policy-2026.pdf", "page": 2, "kind": "text", "expected": "27 working days"},
        {"id": "b", "doc": "hr-leave-policy-2026.pdf", "page": 2, "kind": "number", "expected": "27 working days"},
    ]
    calls = []
    engine = PdfPlumberEngine(lambda path, page: calls.append((path, page)) or "The policy has 27 working days.")
    report = compare_fields(fields, CORPUS, [engine])
    assert report["measurement"] is True and report["page_count"] == 1
    assert len(calls) == 1
    assert report["by_engine_kind"][0]["exact_accuracy"]["mean"] == 1
    assert report["by_engine_kind"][1]["normalized_accuracy"]["lo"] == 1
    assert "95% CI" in _markdown(report)


def test_dryrun_report_is_not_measured_and_refusal_uses_review_guard(tmp_path, capsys):
    fixture = PROJECT_ROOT / "benchmarks" / "datasets" / "parsing" / "synthetic-v1.jsonl"
    out = tmp_path / "dry-run.json"
    args = SimpleNamespace(fields=str(fixture), corpus=str(CORPUS), engines=["pdfplumber", "gemini"],
                           dry_run=True, out=str(out), cache_path=str(tmp_path / "cache.sqlite"),
                           ledger=str(tmp_path / "ledger.jsonl"), spend_cap=None)
    assert run(args) == 0
    report = json.loads(out.read_text())
    assert report["measurement"] is False and "NOT MEASURED" in out.with_suffix(".md").read_text()
    assert not (tmp_path / "cache.sqlite").exists()

    args.dry_run = False
    assert run(args) == 2
    assert "not human-reviewed" in capsys.readouterr().err


def test_measured_report_markdown_formats_confidence_intervals():
    report = {"status": "measured", "measurement": True, "by_engine_kind": [], "by_engine_page": [
        {"engine": "pdfplumber", "page_count": 1,
         "usd_per_page": {"mean": 0.0, "lo": 0.0, "hi": 0.0},
         "seconds_per_page": {"mean": 0.1, "lo": 0.1, "hi": 0.1}}]}
    assert "$0.000000" in _markdown(report)


def test_partial_gemini_failure_records_spend_and_cached_retry_is_free(tmp_path, monkeypatch, capsys):
    import scribe_eval.commands.parse_compare as command

    class PaidThenFail:
        model = "gemini-2.5-flash"
        is_fake = False

        def __init__(self):
            self.calls = []

        async def transcribe(self, png, *, page):
            self.calls.append(page)
            if page == 2:
                raise RuntimeError("second page failed")
            return OcrResult("transcribed page one", 100, 50)

    cache, provider = FakeCache(), PaidThenFail()
    monkeypatch.setattr(command, "DiskCache", lambda path: cache)
    monkeypatch.setattr(command, "GeminiVisionEngine",
                        lambda cache: GeminiVisionEngine(cache=cache, client=provider))
    ledger = tmp_path / "spend.jsonl"
    fields = tmp_path / "fields.jsonl"
    page_fields = [
        {"id": "f1", "doc": "hr-leave-policy-2026.pdf", "page": 1, "field_id": "one", "kind": "text",
         "expected": "one", "reviewed": True},
        {"id": "f2", "doc": "hr-leave-policy-2026.pdf", "page": 2, "field_id": "two", "kind": "text",
         "expected": "two", "reviewed": True},
    ]
    fields.write_text("\n".join(json.dumps(item) for item in page_fields) + "\n")
    args = SimpleNamespace(fields=str(fields), corpus=str(CORPUS), engines=["gemini"], dry_run=False,
                           out=str(tmp_path / "first.json"), cache_path=str(tmp_path / "cache.sqlite"),
                           ledger=str(ledger), spend_cap=1.0)

    assert command.run(args) == 2
    assert "page 2" in capsys.readouterr().err
    original = spend_total(ledger)
    assert original == pytest.approx(tokens_usd("gemini-2.5-flash", 100, 50))
    first_record = read_ledger(ledger)[0]
    assert first_record["live_calls"] == 1 and first_record["usd"] > 0
    assert provider.calls == [1, 2]

    fields.write_text(json.dumps(page_fields[0]) + "\n")
    args.out = str(tmp_path / "retry.json")
    assert command.run(args) == 0
    assert provider.calls == [1, 2]
    assert spend_total(ledger) == original
    retry_record = read_ledger(ledger)[1]
    assert retry_record["cached"] is True and retry_record["usd"] == 0

    args.spend_cap = 0
    assert command.run(args) == 2
    assert "exceeds the $0.00 spend cap" in capsys.readouterr().err
    assert spend_total(ledger) == original


def test_ledger_write_failure_is_visible_without_replacing_extraction_error(tmp_path, monkeypatch, capsys):
    import scribe_eval.commands.parse_compare as command
    from scribe_eval.cost import Usage

    class BrokenGemini:
        name = "gemini"
        model_id = "gemini-2.5-flash"
        usages = [Usage(model_id, 10, 2)]

        def __init__(self, cache):
            pass

        def preflight(self, *args, **kwargs):
            pass

        def write_spend(self, *args, **kwargs):
            raise OSError("ledger is read-only")

    cache = FakeCache()
    monkeypatch.setattr(command, "DiskCache", lambda path: cache)
    monkeypatch.setattr(command, "GeminiVisionEngine", BrokenGemini)
    monkeypatch.setattr(command, "compare_fields", lambda *args: (_ for _ in ()).throw(RuntimeError("extraction failed")))
    fields = tmp_path / "fields.jsonl"
    fields.write_text(json.dumps({"id": "field", "doc": "hr-leave-policy-2026.pdf", "page": 1,
                                  "field_id": "x", "kind": "text", "expected": "x", "reviewed": True}) + "\n")
    args = SimpleNamespace(fields=str(fields), corpus=str(CORPUS), engines=["gemini"], dry_run=False,
                           out=str(tmp_path / "out.json"), cache_path=str(tmp_path / "cache.sqlite"),
                           ledger=str(tmp_path / "ledger.jsonl"), spend_cap=None)
    assert command.run(args) == 2
    err = capsys.readouterr().err
    assert "extraction failed" in err and "could not record Gemini spend" in err

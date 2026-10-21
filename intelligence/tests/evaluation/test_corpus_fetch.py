"""A2: scribe-eval fetch-corpus. Offline: httpx.MockTransport only, PDFs from fixtures/corpus plus tiny
hand-built PDFs (an image page with a hidden text layer, a ruled table)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scribe_eval import corpus_fetch as cf
from scribe_ingest.pdf import OCR_CHAR_THRESHOLD

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "corpus"
SCANNED = (FIXTURES / "facility-safety-notice-scanned.pdf").read_bytes()
TEXT = (FIXTURES / "expense-policy.pdf").read_bytes()


def make_pdf(*, text: str | None = None, invisible: bool = False, image: bool = False, table: bool = False) -> bytes:
    """One-page PDF: optional text (optionally render mode 3, i.e. a hidden OCR layer), image, ruled 2x2 table."""
    ops = []
    if text:
        ops.append(f"BT /F1 12 Tf {'3 Tr ' if invisible else ''}72 740 Td ({text}) Tj ET")
    if table:
        for y in (600, 580, 560):
            ops.append(f"100 {y} m 300 {y} l S")
        for x in (100, 200, 300):
            ops.append(f"{x} 560 m {x} 600 l S")
        ops.append("BT /F1 10 Tf 110 585 Td (a) Tj 100 0 Td (b) Tj -100 -20 Td (c) Tj 100 0 Td (d) Tj ET")
    if image:
        ops.append("q 300 0 0 300 100 200 cm /Im1 Do Q")
    content = "\n".join(ops).encode()
    xobj = "/XObject << /Im1 6 0 R >>" if image else ""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        f"/Resources << /Font << /F1 5 0 R >> {xobj} >> >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if image:
        objs.append(
            b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Length 4 >>\nstream\n\x00\xff\xff\x00\nendstream"
        )
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


OCR_LAYER_PDF = make_pdf(text="OCR text layer of a scanned page about data encryption", invisible=True, image=True)
TABLE_PDF = make_pdf(text="A ruled table follows on this page of the document", table=True)


def write_pdf(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def doc(did: str, title: str = "Some Title", **extra) -> dict:
    d = {
        "id": did,
        "title": title,
        "url": f"https://example.test/{did}.pdf",
        "sha256": None,
        "revision_pair": None,
        "revision_role": None,
        "ingest_file": f"{did}.pdf",
        "derived": None,
    }
    d.update(extra)
    return d


def write_manifest(tmp: Path, docs: list[dict]) -> Path:
    path = tmp / "manifest.json"
    path.write_text(json.dumps({"corpus": "t", "version": 1, "status": "draft", "documents": docs}), encoding="utf-8")
    return path


class Server:
    """MockTransport backend: url -> (status, bytes); counts requests per URL."""

    def __init__(self, routes: dict[str, tuple[int, bytes]]):
        self.routes = routes
        self.hits: dict[str, int] = {}

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            self.hits[url] = self.hits.get(url, 0) + 1
            if url not in self.routes:
                raise AssertionError(f"request to a URL that is not in the manifest: {url}")
            status, body = self.routes[url]
            return httpx.Response(status, content=body)

        return httpx.Client(transport=httpx.MockTransport(handler))

    @property
    def total(self) -> int:
        return sum(self.hits.values())


def quiet(_msg: str) -> None:
    return None


def run(manifest: Path, out: Path, server: Server, **kw) -> int:
    return cf.run_fetch(manifest, out, client=server.client(), echo=quiet, **kw)


def passing_corpus(tmp: Path) -> tuple[list[dict], dict[str, tuple[int, bytes]]]:
    """Five revision pairs: v1 a scanned page, v2 a ruled-table page (50% scanned, 50% table-heavy)."""
    docs, routes = [], {}
    for i in range(5):
        a = doc(f"p{i}-v1", revision_pair=f"p{i}", revision_role="v1")
        b = doc(f"p{i}-v2", "A ruled table document", revision_pair=f"p{i}", revision_role="v2")
        docs += [a, b]
        routes[a["url"]] = (200, SCANNED)
        routes[b["url"]] = (200, TABLE_PDF)
    return docs, routes


# ------------------------------------------------------------------ classification


def test_scanned_page_uses_the_ingest_rule(tmp_path: Path) -> None:
    pages, _ = cf.measure_pdf(write_pdf(tmp_path / "s.pdf", SCANNED))
    assert pages[0]["chars"] < OCR_CHAR_THRESHOLD and pages[0]["has_image"]
    assert pages[0]["scanned"] and not pages[0]["ocr_layer"] and not pages[0]["table_heavy"]


def test_text_pdf_is_none_of_the_classes(tmp_path: Path) -> None:
    pages, _ = cf.measure_pdf(write_pdf(tmp_path / "t.pdf", TEXT))
    assert len(pages) == 3
    assert not any(p["scanned"] or p["ocr_layer"] or p["table_heavy"] for p in pages)


def test_ocr_layer_page_is_not_scanned(tmp_path: Path) -> None:
    (p,), _ = cf.measure_pdf(write_pdf(tmp_path / "o.pdf", OCR_LAYER_PDF))
    assert p["has_image"] and p["chars"] >= OCR_CHAR_THRESHOLD
    assert p["ocr_layer"] and not p["scanned"]


def test_table_heavy_page(tmp_path: Path) -> None:
    (p,), _ = cf.measure_pdf(write_pdf(tmp_path / "tb.pdf", TABLE_PDF))
    assert p["table_heavy"] and not p["scanned"]


# ------------------------------------------------------------------ rasterising


def test_rasterize_makes_text_pdf_count_as_scanned(tmp_path: Path) -> None:
    src = write_pdf(tmp_path / "t.pdf", TEXT)
    n = cf.rasterize_pdf(src, tmp_path / "t.raster.pdf", dpi=72)
    pages, _ = cf.measure_pdf(tmp_path / "t.raster.pdf")
    assert n == len(pages) == 3
    assert all(p["scanned"] and not p["ocr_layer"] for p in pages)
    assert src.read_bytes() == TEXT  # the original is untouched


def test_wants_raster_rules() -> None:
    summary = {"pages": 4, "image_pages": 4, "ocr_layer_pages": 4}
    assert cf.wants_raster({"scan_with_ocr_layer": "unknown"}, summary)
    assert not cf.wants_raster({}, summary)  # born-digital docs are never rasterized
    assert not cf.wants_raster({"scan_with_ocr_layer": "unknown"}, {**summary, "ocr_layer_pages": 1})
    assert not cf.wants_raster({"scan_with_ocr_layer": "unknown"}, {"pages": 4, "image_pages": 0, "ocr_layer_pages": 0})
    big = {"pages": cf.MAX_SCANNED_PER_DOC + 1, "image_pages": 60, "ocr_layer_pages": 60}
    assert not cf.wants_raster({"scan_with_ocr_layer": "unknown"}, big)  # would exceed the OCR page cap


def test_run_rasterizes_ocr_layer_scan_and_records_derived(tmp_path: Path) -> None:
    d = doc("old-scan", scan_with_ocr_layer="unknown")
    manifest = write_manifest(tmp_path, [d])
    server = Server({d["url"]: (200, OCR_LAYER_PDF)})
    out = tmp_path / "out"
    run(manifest, out, server, pin=True, rasterize=True)
    rec = json.loads(manifest.read_text())["documents"][0]
    assert rec["ingest_file"] == "old-scan.raster.pdf"
    assert rec["scan_with_ocr_layer"] is True
    assert rec["derived"]["kind"] == "rasterized-image-only" and rec["derived"]["dpi"] == 200
    assert rec["derived"]["source_sha256"] == rec["sha256"] == cf.sha256_bytes(OCR_LAYER_PDF)
    assert rec["derived"]["sha256"] == cf.sha256_file(out / "old-scan.raster.pdf") != rec["sha256"]
    stats = json.loads((tmp_path / "page-stats.json").read_text())["docs"]["old-scan"]
    assert stats["scanned_pages"] == 1 and stats["ocr_layer_pages"] == 0
    assert stats["original"]["ocr_layer_pages"] == 1 and stats["original"]["scanned_pages"] == 0
    # second run (no --pin) verifies both hashes without touching the network
    assert run(manifest, out, server, rasterize=True) in (0, 1)
    assert server.total == 1
    assert json.loads(manifest.read_text())["documents"][0]["derived"] == rec["derived"]


def test_derived_hash_mismatch_fails_without_pin(tmp_path: Path) -> None:
    d = doc("old-scan", scan_with_ocr_layer="unknown")
    manifest = write_manifest(tmp_path, [d])
    server = Server({d["url"]: (200, OCR_LAYER_PDF)})
    out = tmp_path / "out"
    run(manifest, out, server, pin=True, rasterize=True)
    raster = out / "old-scan.raster.pdf"
    raster.write_bytes(raster.read_bytes() + b"\n%x")
    assert run(manifest, out, server, rasterize=True) == 1


# ------------------------------------------------------------------ pinning, verification, download rules


def test_pin_writes_sha256_and_verify_passes(tmp_path: Path) -> None:
    docs, routes = passing_corpus(tmp_path)
    manifest = write_manifest(tmp_path, docs)
    server = Server(routes)
    out = tmp_path / "out"
    assert run(manifest, out, server, pin=True) == 0
    pinned = json.loads(manifest.read_text())["documents"]
    assert pinned[0]["sha256"] == cf.sha256_bytes(SCANNED)
    assert pinned[1]["sha256"] == cf.sha256_bytes(TABLE_PDF)
    assert all(p["ingest_file"] == f"{p['id']}.pdf" and p["derived"] is None for p in pinned)
    assert run(manifest, out, server) == 0  # verification run


def test_unpinned_manifest_without_pin_fails_before_network(tmp_path: Path) -> None:
    docs, routes = passing_corpus(tmp_path)
    manifest = write_manifest(tmp_path, docs)
    server = Server(routes)
    assert run(manifest, tmp_path / "out", server) == 1
    assert server.total == 0


def test_existing_file_with_wrong_sha256_fails_loudly(tmp_path: Path) -> None:
    docs, routes = passing_corpus(tmp_path)
    manifest = write_manifest(tmp_path, docs)
    out = tmp_path / "out"
    server = Server(routes)
    assert run(manifest, out, server, pin=True) == 0
    (out / "p0-v1.pdf").write_bytes(TEXT)
    assert run(manifest, out, server) == 1
    stats = json.loads((tmp_path / "page-stats.json").read_text())
    assert stats["docs"]["p0-v1"]["status"] == "error" and "sha256 mismatch" in stats["docs"]["p0-v1"]["error"]


def test_downloaded_sha256_mismatch_fails_and_writes_nothing(tmp_path: Path) -> None:
    d = doc("a", sha256="0" * 64)
    manifest = write_manifest(tmp_path, [d])
    out = tmp_path / "out"
    assert run(manifest, out, Server({d["url"]: (200, TEXT)})) == 1
    assert not (out / "a.pdf").exists()


def test_non_pdf_response_is_rejected(tmp_path: Path) -> None:
    d = doc("a")
    manifest = write_manifest(tmp_path, [d])
    out = tmp_path / "out"
    server = Server({d["url"]: (200, b"<html>Access denied</html>")})
    assert run(manifest, out, server, pin=True) == 1
    assert not (out / "a.pdf").exists()
    assert json.loads(manifest.read_text())["documents"][0]["sha256"] is None
    stats = json.loads((tmp_path / "page-stats.json").read_text())
    assert stats["docs"]["a"]["status"] == "fetch_failed" and stats["fetch_failed"] == ["a"]
    with pytest.raises(cf.FetchFailed):
        cf.download_pdf(server.client(), d["url"])


def test_http_error_is_retried_once_then_marked_fetch_failed(tmp_path: Path) -> None:
    d = doc("a")
    manifest = write_manifest(tmp_path, [d])
    server = Server({d["url"]: (503, b"")})
    assert run(manifest, tmp_path / "out", server, pin=True) == 1
    assert server.hits[d["url"]] == 2
    assert json.loads((tmp_path / "page-stats.json").read_text())["fetch_failed"] == ["a"]


def test_already_fetched_files_are_skipped(tmp_path: Path) -> None:
    docs, routes = passing_corpus(tmp_path)
    manifest = write_manifest(tmp_path, docs)
    out = tmp_path / "out"
    first = Server(routes)
    run(manifest, out, first, pin=True)
    assert first.total == len(docs)
    second = Server(routes)
    assert run(manifest, out, second) == 0
    assert second.total == 0


def test_redirects_are_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.test/final.pdf"})
        return httpx.Response(200, content=TEXT)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert cf.download_pdf(client, "https://example.test/start") == TEXT


# ------------------------------------------------------------------ identity check


def test_identity_check_by_number_words_and_unknown() -> None:
    assert cf.identity_check("FIPS PUB 46-1: Data Encryption Standard (DES), 1988", "FIPS PUB 46-1\nGarbled stuff here and more text") == "pass"
    assert cf.identity_check("AC 20-42A: Hand Fire Extinguishers for Use in Aircraft", "ADVISORY CIRCULAR AC 20-42A of 1980 text text") == "pass"
    assert cf.identity_check("SP 800-88 Rev. 1: Guidelines for Media Sanitization", "NIST Special Publication 800-88 Revision 1 Guidelines for Media Sanitization") == "pass"
    assert cf.identity_check("General Records Schedule 1.1: Financial Management and Reporting Records", "Financial management and reporting records schedule") == "pass"
    assert cf.identity_check("FIPS 180-4: Secure Hash Standard (SHS)", "Annual report of the cafeteria committee, budget and menus") == "fail"
    assert cf.identity_check("FIPS 180-4: Secure Hash Standard (SHS)", "  \n ") == "unknown"


def test_identity_check_is_recorded_without_failing_the_run(tmp_path: Path) -> None:
    docs, routes = passing_corpus(tmp_path)
    docs[1]["title"] = "FIPS 999: Quantum Toaster Standard"  # the served PDF is a table page about something else
    manifest = write_manifest(tmp_path, docs)
    assert run(manifest, tmp_path / "out", Server(routes), pin=True) == 0
    stats = json.loads((tmp_path / "page-stats.json").read_text())
    assert stats["identity_check_failures"] == ["p0-v2"]
    assert stats["docs"]["p0-v2"]["identity_check"] == "fail"
    assert stats["docs"]["p0-v1"]["identity_check"] == "unknown"  # image-only, no text layer


# ------------------------------------------------------------------ thresholds


def _totals(**over) -> dict:
    base = {"pages": 1000, "scanned_share": 0.3, "table_heavy_share": 0.35, "revision_pairs": 6, "max_scanned_per_doc": 44}
    return {**base, **over}


def test_thresholds_pass() -> None:
    assert cf.check_thresholds(_totals()) == []


@pytest.mark.parametrize(
    "over, fragment",
    [
        ({"scanned_share": 0.19}, "scanned share"),
        ({"table_heavy_share": 0.29}, "table-heavy share"),
        ({"revision_pairs": 4}, "revision pairs"),
        ({"revision_pairs": 9}, "revision pairs"),
        ({"pages": 1501}, "total pages"),
        ({"max_scanned_per_doc": 51}, "scanned pages"),
    ],
)
def test_thresholds_fail(over: dict, fragment: str) -> None:
    fails = cf.check_thresholds(_totals(**over))
    assert len(fails) == 1 and fragment in fails[0]


def test_run_exits_1_when_a_threshold_fails(tmp_path: Path) -> None:
    d = doc("only", revision_pair=None)
    manifest = write_manifest(tmp_path, [d])
    assert run(manifest, tmp_path / "out", Server({d["url"]: (200, TEXT)}), pin=True) == 1


def test_revision_pair_needs_both_members() -> None:
    docs = [
        doc("a1", revision_pair="a", revision_role="v1"),
        doc("a2", revision_pair="a", revision_role="v2"),
        doc("b1", revision_pair="b", revision_role="v1"),
        doc("b2", revision_pair="b", revision_role="v2"),
    ]
    assert cf.revision_pairs(docs, {"a1", "a2", "b1"}) == 1
    assert cf.revision_pairs(docs, {"a1", "a2", "b1", "b2"}) == 2


def test_stats_file_has_one_line_per_page(tmp_path: Path) -> None:
    docs, routes = passing_corpus(tmp_path)
    manifest = write_manifest(tmp_path, docs)
    run(manifest, tmp_path / "out", Server(routes), pin=True)
    text = (tmp_path / "page-stats.json").read_text()
    assert text.count('"chars"') == 10 and all(l.count("chars") == 1 for l in text.splitlines() if "chars" in l)
    totals = json.loads(text)["totals"]
    assert totals["pages"] == 10 and totals["scanned_pages"] == 5 and totals["table_heavy_pages"] == 5

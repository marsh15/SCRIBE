"""gov-v1 corpus fetch (A2): download the manifest's PDFs, pin and verify sha256, measure pages.

Only URLs listed in the manifest are requested. PDFs land in a git-ignored directory
(``fixtures/corpus-gov/``). Downloaded files are data: they are parsed with pdfplumber /
pypdfium2 and never executed.

Page classes (per page, measured on the file that is ingested):

* ``scanned``      the ingest parser's OCR rule (`scribe_ingest.pdf`): fewer than
                   ``OCR_CHAR_THRESHOLD`` non-space characters AND at least one image.
* ``ocr_layer``    an image page whose text layer has ``OCR_CHAR_THRESHOLD`` or more characters
                   (a scan with a hidden OCR text layer; the ingest parser would NOT OCR it).
* ``table_heavy``  pdfplumber ``find_tables`` (lines strategy) finds a table with 2+ rows and 2+ cols.

Old scans that carry a hidden OCR layer would bypass the real OCR path, so with
``--rasterize-ocr-layer`` such documents get a derived image-only copy
(``<id>.raster.pdf``, pypdfium2 render at 200 dpi grayscale, assembled with Pillow). The
manifest records both sha256s and which file is ingested.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import httpx

from scribe_ingest.ocr import MAX_OCR_PAGES
from scribe_ingest.pdf import OCR_CHAR_THRESHOLD

RASTER_DPI = 200
RASTER_SUFFIX = ".raster.pdf"
MIN_FREE_BYTES = 2 * 1024**3
USER_AGENT = "scribe-eval-fetch-corpus/1.0 (research corpus; manifest URLs only)"
IDENTITY_PAGES = 3

# Acceptance thresholds, asserted on the INGEST files.
MIN_SCANNED_SHARE = 0.20
MIN_TABLE_HEAVY_SHARE = 0.30
PAIR_RANGE = (5, 8)
MAX_TOTAL_PAGES = 1500
MAX_SCANNED_PER_DOC = MAX_OCR_PAGES

_STOPWORDS = frozenset(
    "the and for with that this from using into use used guide guidelines version rev revision updated".split()
)
_SERIES = (
    "general records schedule",
    "fips pub",
    "publication",
    "osha",
    "fips",
    "pub",
    "grs",
    "sp",
    "ac",
)
_SERIES_ALIASES = {"sp": ("sp", "special publication"), "ac": ("ac", "advisory circular")}


class FetchError(Exception):
    """A document could not be verified (sha256 mismatch, bad manifest state, ...)."""


class FetchFailed(FetchError):
    """The URL could not be fetched (network/HTTP error, or the response is not a PDF)."""


# ------------------------------------------------------------------ hashing and manifest io


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ download


def download_pdf(client: httpx.Client, url: str, *, attempts: int = 2) -> bytes:
    """GET `url` (redirects followed); retry once; require a ``%PDF`` header."""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            resp = client.get(url, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            last = FetchFailed(f"{type(exc).__name__}: {exc}")
            continue
        data = resp.content
        if not data.startswith(b"%PDF"):
            # A wrong content type is not transient: do not retry.
            raise FetchFailed(f"response is not a PDF (no %PDF header; {len(data)} bytes)")
        return data
    assert last is not None
    raise last


# ------------------------------------------------------------------ page classification


def _page_stat(page: Any) -> dict[str, Any]:
    chars = sum(1 for c in page.chars if c["text"].strip())
    has_image = len(page.images) >= 1
    return {
        "chars": chars,
        "has_image": has_image,
        "scanned": chars < OCR_CHAR_THRESHOLD and has_image,  # same rule as scribe_ingest.pdf
        "ocr_layer": has_image and chars >= OCR_CHAR_THRESHOLD,
        "table_heavy": _table_heavy(page),
    }


def _table_heavy(page: Any) -> bool:
    try:
        tables = page.find_tables({"vertical_strategy": "lines", "horizontal_strategy": "lines"})
    except Exception:
        return False
    for t in tables:
        try:
            rows = t.extract()
        except Exception:
            continue
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
            return True
    return False


def measure_pdf(path: Path, *, text_pages: int = 0) -> tuple[list[dict[str, Any]], str]:
    """Per-page stats, plus the text of the first `text_pages` pages (for the identity check)."""
    import pdfplumber

    stats: list[dict[str, Any]] = []
    texts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            stats.append(_page_stat(page))
            if i < text_pages:
                try:
                    texts.append(page.extract_text() or "")
                except Exception:
                    texts.append("")
            page.flush_cache()
    return stats, "\n".join(texts)


def summarise(pages: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pages": len(pages),
        "scanned_pages": sum(p["scanned"] for p in pages),
        "ocr_layer_pages": sum(p["ocr_layer"] for p in pages),
        "table_heavy_pages": sum(p["table_heavy"] for p in pages),
        "image_pages": sum(p["has_image"] for p in pages),
    }


# ------------------------------------------------------------------ identity check


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _number_phrases(title: str) -> list[str]:
    """Normalised phrases that carry the document number, e.g. ``fips pub 46``, ``ac 20 42a``."""
    toks = _norm(title).split()
    for series in _SERIES:
        s = series.split()
        for i in range(len(toks) - len(s)):
            if toks[i : i + len(s)] != s:
                continue
            nums: list[str] = []
            for t in toks[i + len(s) : i + len(s) + 3]:
                if not t[0].isdigit():
                    break
                nums.append(t)
            if not nums:
                continue
            tail = " ".join(nums)
            heads = _SERIES_ALIASES.get(series, (series,))
            phrases = [f"{h} {tail}" for h in heads]
            if len(nums) >= 2:  # "800 88": distinctive enough on its own
                phrases.append(tail)
            return phrases
    return []


def _title_words(title: str) -> list[str]:
    words = []
    for w in _norm(title).split():
        if len(w) >= 4 and not w.isdigit() and w not in _STOPWORDS and w not in words:
            words.append(w)
    return words


def identity_check(title: str, text: str) -> str:
    """``pass`` / ``fail`` / ``unknown`` (no usable text in the sample)."""
    norm = _norm(text)
    if len(norm.replace(" ", "")) < OCR_CHAR_THRESHOLD:
        return "unknown"
    padded = f" {norm} "
    for phrase in _number_phrases(title):
        if f" {phrase} " in padded:
            return "pass"
    words = _title_words(title)
    if words and sum(f" {w} " in padded or w in norm for w in words) / len(words) >= 0.6:
        return "pass"
    return "fail"


# ------------------------------------------------------------------ rasterising


def rasterize_pdf(src: Path, dst: Path, dpi: int = RASTER_DPI) -> int:
    """Render every page of `src` (pypdfium2, grayscale) and assemble an image-only PDF at `dst`."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(src))
    images = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                bitmap = page.render(scale=dpi / 72, grayscale=True)
                try:
                    images.append(bitmap.to_pil().convert("L"))
                finally:
                    bitmap.close()
            finally:
                page.close()
    finally:
        pdf.close()
    if not images:
        raise FetchError(f"{src.name}: no pages to rasterize")
    tmp = dst.with_name(dst.name + ".part")
    try:
        images[0].save(tmp, "PDF", save_all=True, append_images=images[1:], resolution=float(dpi))
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
        for im in images:
            im.close()
    return len(images)


def wants_raster(doc: dict[str, Any], summary: dict[str, int]) -> bool:
    """A declared old scan whose image pages mostly carry an OCR text layer (and fits the OCR cap)."""
    if doc.get("scan_with_ocr_layer") is None:
        return False
    if summary["image_pages"] == 0 or summary["pages"] > MAX_SCANNED_PER_DOC:
        return False
    return summary["ocr_layer_pages"] * 2 > summary["image_pages"]


# ------------------------------------------------------------------ thresholds


def revision_pairs(docs: list[dict[str, Any]], present: set[str]) -> int:
    """Count revision pairs whose two members (roles v1 and v2) are both present."""
    roles: dict[str, set[str]] = {}
    for d in docs:
        if d.get("revision_pair") and d["id"] in present:
            roles.setdefault(d["revision_pair"], set()).add(d.get("revision_role"))
    return sum(1 for r in roles.values() if {"v1", "v2"} <= r)


def totals_for(ingest: dict[str, list[dict[str, Any]]], docs: list[dict[str, Any]]) -> dict[str, Any]:
    pages = sum(len(p) for p in ingest.values())
    scanned = sum(sum(x["scanned"] for x in p) for p in ingest.values())
    tables = sum(sum(x["table_heavy"] for x in p) for p in ingest.values())
    return {
        "docs": len(ingest),
        "pages": pages,
        "scanned_pages": scanned,
        "scanned_share": round(scanned / pages, 4) if pages else 0.0,
        "ocr_layer_pages": sum(sum(x["ocr_layer"] for x in p) for p in ingest.values()),
        "table_heavy_pages": tables,
        "table_heavy_share": round(tables / pages, 4) if pages else 0.0,
        "revision_pairs": revision_pairs(docs, set(ingest)),
        "max_scanned_per_doc": max((sum(x["scanned"] for x in p) for p in ingest.values()), default=0),
    }


def check_thresholds(totals: dict[str, Any]) -> list[str]:
    fails = []
    if totals["scanned_share"] < MIN_SCANNED_SHARE:
        fails.append(f"scanned share {totals['scanned_share']:.1%} < {MIN_SCANNED_SHARE:.0%}")
    if totals["table_heavy_share"] < MIN_TABLE_HEAVY_SHARE:
        fails.append(f"table-heavy share {totals['table_heavy_share']:.1%} < {MIN_TABLE_HEAVY_SHARE:.0%}")
    lo, hi = PAIR_RANGE
    if not lo <= totals["revision_pairs"] <= hi:
        fails.append(f"revision pairs {totals['revision_pairs']} not in {lo}-{hi}")
    if totals["pages"] > MAX_TOTAL_PAGES:
        fails.append(f"total pages {totals['pages']} > {MAX_TOTAL_PAGES}")
    if totals["max_scanned_per_doc"] > MAX_SCANNED_PER_DOC:
        fails.append(f"a document has {totals['max_scanned_per_doc']} scanned pages > {MAX_SCANNED_PER_DOC}")
    return fails


# ------------------------------------------------------------------ stats file


def dump_stats(stats: dict[str, Any]) -> str:
    """JSON with one line per page record (keeps the committed file readable and small)."""
    slots: list[str] = []

    def hoist(page: dict[str, Any]) -> str:
        slots.append(json.dumps(page, separators=(", ", ": ")))
        return f"@@{len(slots) - 1}@@"

    copy = json.loads(json.dumps(stats))
    for d in copy["docs"].values():
        d["page_stats"] = [hoist(p) for p in d.get("page_stats", [])]
        if d.get("original"):
            d["original"]["page_stats"] = [hoist(p) for p in d["original"].get("page_stats", [])]
    text = json.dumps(copy, indent=2, ensure_ascii=False)
    return re.sub(r'"@@(\d+)@@"', lambda m: slots[int(m.group(1))], text) + "\n"


# ------------------------------------------------------------------ the run


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _ensure_original(
    doc: dict[str, Any], out: Path, client: httpx.Client, pin: bool, log: list[str]
) -> Path:
    """Return the verified original PDF path (downloading if absent). Raises FetchError."""
    path = out / f"{doc['id']}.pdf"
    pinned = doc.get("sha256")
    if not pinned and not pin:
        raise FetchError("manifest has no sha256 for this document; run once with --pin")
    if path.exists():
        digest = sha256_file(path)
        if pinned and digest != pinned:
            raise FetchError(
                f"sha256 mismatch for existing file {path.name}: manifest {pinned[:12]}.., file {digest[:12]}.. "
                "(delete the file to refetch; null the manifest sha256 only to deliberately re-pin)"
            )
        if not pinned:
            if not path.read_bytes()[:4] == b"%PDF":
                raise FetchError(f"{path.name} is not a PDF")
            doc["sha256"] = digest
            log.append(f"pinned existing {path.name}")
        return path
    data = download_pdf(client, doc["url"])
    digest = sha256_bytes(data)
    if pinned and digest != pinned:
        raise FetchError(f"sha256 mismatch for {doc['url']}: manifest {pinned[:12]}.., downloaded {digest[:12]}..")
    path.write_bytes(data)
    if not pinned:
        doc["sha256"] = digest
        log.append(f"pinned {path.name}")
    return path


def _ensure_raster(
    doc: dict[str, Any], original: Path, out: Path, pin: bool, summary: dict[str, int], make: bool
) -> tuple[Path, dict[str, Any]] | None:
    """Return (raster path, derived record) or None when this document is ingested as-is."""
    prior = doc.get("derived")
    wanted = wants_raster(doc, summary) if make else prior is not None
    if not wanted:
        return None
    dst = out / f"{doc['id']}{RASTER_SUFFIX}"
    src_sha = sha256_file(original)
    if prior and prior.get("source_sha256") not in (None, src_sha):
        raise FetchError("manifest derived.source_sha256 does not match the original file")
    if not dst.exists():
        if not make:
            raise FetchError(f"{dst.name} is missing; rerun with --rasterize-ocr-layer")
        if _free_bytes(out) < MIN_FREE_BYTES:
            raise FetchError("less than 2 GiB free disk; refusing to rasterize")
        rasterize_pdf(original, dst, RASTER_DPI)
    sha = sha256_file(dst)
    record = {"kind": "rasterized-image-only", "dpi": RASTER_DPI, "source_sha256": src_sha, "sha256": sha}
    if prior and prior.get("sha256") and prior["sha256"] != sha and not pin:
        raise FetchError(
            f"sha256 mismatch for {dst.name}: manifest {prior['sha256'][:12]}.., file {sha[:12]}.. "
            "(rendering can differ across pdfium/Pillow versions; use --pin to re-record)"
        )
    return dst, record


def run_fetch(
    manifest_path: Path,
    out: Path,
    *,
    pin: bool = False,
    rasterize: bool = False,
    stats_path: Path | None = None,
    client: httpx.Client | None = None,
    echo: Any = print,
) -> int:
    """Fetch, verify, measure, assert. Returns a process exit code (0 only if everything passes)."""
    manifest = load_manifest(manifest_path)
    docs: list[dict[str, Any]] = manifest["documents"]
    stats_path = stats_path or manifest_path.with_name("page-stats.json")
    out.mkdir(parents=True, exist_ok=True)
    if _free_bytes(out) < MIN_FREE_BYTES:
        echo("ERROR: less than 2 GiB free disk space; stopping")
        return 1

    own_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(60.0), headers={"User-Agent": USER_AGENT})
    problems: list[str] = []
    ingest: dict[str, list[dict[str, Any]]] = {}
    original_pages: dict[str, list[dict[str, Any]]] = {}
    doc_stats: dict[str, Any] = {}
    failures_identity: list[str] = []
    before = json.dumps(manifest, sort_keys=True)
    try:
        for doc in docs:
            did = doc["id"]
            entry: dict[str, Any] = {"status": "ok"}
            doc_stats[did] = entry
            log: list[str] = []
            try:
                original = _ensure_original(doc, out, http, pin, log)
                o_pages, text = measure_pdf(original, text_pages=IDENTITY_PAGES)
                o_sum = summarise(o_pages)
                entry["identity_check"] = identity_check(doc["title"], text)
                if entry["identity_check"] == "fail":
                    failures_identity.append(did)
                if doc.get("scan_with_ocr_layer") is not None and pin and o_sum["image_pages"]:
                    doc["scan_with_ocr_layer"] = o_sum["ocr_layer_pages"] * 2 > o_sum["image_pages"]
                derived = _ensure_raster(doc, original, out, pin, o_sum, rasterize)
            except (FetchError, OSError) as exc:
                entry["status"] = "fetch_failed" if isinstance(exc, FetchFailed) else "error"
                entry["error"] = str(exc)
                problems.append(f"{did}: {exc}")
                continue
            if derived is None:
                ingest_path, d_rec = original, None
                pages = o_pages
            else:
                ingest_path, d_rec = derived
                pages, _ = measure_pdf(ingest_path)
                if len(pages) != len(o_pages) or not all(p["scanned"] for p in pages):
                    problems.append(f"{did}: raster copy does not count as scanned under the ingest rule")
                    entry["status"] = "error"
                    continue
            doc["ingest_file"] = ingest_path.name
            doc["derived"] = d_rec
            ingest[did] = pages
            original_pages[did] = o_pages
            entry.update(
                {
                    "ingest_file": ingest_path.name,
                    "sha256": doc["sha256"],
                    **summarise(pages),
                    "page_stats": pages,
                }
            )
            if d_rec:
                entry["original"] = {
                    "sha256": doc["sha256"],
                    **summarise(o_pages),
                    "page_stats": o_pages,
                }
            echo(f"  {did}: {len(pages)}p, scanned {entry['scanned_pages']}, tables {entry['table_heavy_pages']}"
                 f"{' [rasterized]' if d_rec else ''} identity={entry['identity_check']}" + (f" ({'; '.join(log)})" if log else ""))
    finally:
        if own_client:
            http.close()

    totals = totals_for(ingest, docs)
    fetch_failed = [d for d, e in doc_stats.items() if e["status"] != "ok"]
    threshold_fails = check_thresholds(totals)
    stats = {
        "corpus": manifest.get("corpus"),
        "note": "Measured on the ingest files (the derived raster copy where present). "
        "scanned = chars < OCR_CHAR_THRESHOLD and an image; ocr_layer = image page with a text layer; "
        "table_heavy = pdfplumber lines-strategy table with 2+ rows and 2+ cols.",
        "thresholds": {
            "min_scanned_share": MIN_SCANNED_SHARE,
            "min_table_heavy_share": MIN_TABLE_HEAVY_SHARE,
            "revision_pairs": list(PAIR_RANGE),
            "max_total_pages": MAX_TOTAL_PAGES,
            "max_scanned_per_doc": MAX_SCANNED_PER_DOC,
        },
        "ocr_char_threshold": OCR_CHAR_THRESHOLD,
        "totals": totals,
        "original_totals": totals_for(original_pages, docs),
        "identity_check_failures": failures_identity,
        "fetch_failed": fetch_failed,
        "docs": doc_stats,
    }
    stats_path.write_text(dump_stats(stats), encoding="utf-8")
    if json.dumps(manifest, sort_keys=True) != before:
        if pin:
            if all(d.get("sha256") for d in docs):
                manifest["status"] = "pinned; sha256 and ingest_file/derived measured by scribe-eval fetch-corpus"
            save_manifest(manifest_path, manifest)
            echo(f"manifest updated: {manifest_path}")
        else:
            problems.append("manifest is stale (ingest_file/derived differ from what was measured); rerun with --pin")

    _print_totals(echo, totals, stats["original_totals"])
    if failures_identity:
        echo("identity check FAILED (not fatal): " + ", ".join(failures_identity))
    for p in problems:
        echo(f"PROBLEM: {p}")
    for f in threshold_fails:
        echo(f"THRESHOLD FAILED: {f}")
    return 1 if (problems or threshold_fails) else 0


def _print_totals(echo: Any, t: dict[str, Any], o: dict[str, Any]) -> None:
    rows = [
        ("docs", t["docs"], o["docs"]),
        ("pages", t["pages"], o["pages"]),
        ("scanned pages", f"{t['scanned_pages']} ({t['scanned_share']:.1%})", f"{o['scanned_pages']} ({o['scanned_share']:.1%})"),
        ("ocr_layer pages", t["ocr_layer_pages"], o["ocr_layer_pages"]),
        ("table-heavy pages", f"{t['table_heavy_pages']} ({t['table_heavy_share']:.1%})", f"{o['table_heavy_pages']} ({o['table_heavy_share']:.1%})"),
        ("revision pairs", t["revision_pairs"], o["revision_pairs"]),
        ("max scanned/doc", t["max_scanned_per_doc"], o["max_scanned_per_doc"]),
    ]
    echo(f"{'':20}{'ingest files':>18}{'original files':>18}")
    for name, a, b in rows:
        echo(f"{name:20}{a!s:>18}{b!s:>18}")


def run(args: argparse.Namespace) -> int:
    return run_fetch(
        Path(args.manifest),
        Path(args.out),
        pin=args.pin,
        rasterize=args.rasterize_ocr_layer,
        stats_path=Path(args.stats) if args.stats else None,
    )

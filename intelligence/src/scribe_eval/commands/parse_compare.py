"""Compare page text extractors against reviewed parsing field expectations."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..cache import DiskCache
from ..cost import CAP_USD, SpendError
from ..metrics import bootstrap_ci_mean
from ..parsing.dataset import FieldDatasetError, load_fields, safe_doc_path
from ..parsing.engines import GeminiVisionEngine, PdfPlumberEngine, TesseractEngine
from ..parsing.score import match_expected
from ..review.require import refusal_message, unreviewed_ids

ENGINE_NAMES = ("pdfplumber", "tesseract", "gemini")


class _NamedEngine:
    def __init__(self, name: str) -> None:
        self.name = name


def _ci(values: list[float], seed: int) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "lo": None, "hi": None}
    lo, hi = bootstrap_ci_mean(values, seed=seed)
    return {"mean": sum(values) / len(values), "lo": lo, "hi": hi}


def _page_keys(fields: list[dict[str, Any]]) -> list[tuple[str, int]]:
    return sorted({(str(item["doc"]), int(item["page"] or 1)) for item in fields})


def compare_fields(
    fields: list[dict[str, Any]],
    corpus: str | Path,
    engines: list[Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Extract each engine/doc/page once, then score each field against its page text."""
    if dry_run:
        return {
            "measurement": False,
            "status": "dry_run",
            "field_count": len(fields),
            "page_count": len(_page_keys(fields)),
            "engine_names": [e.name for e in engines],
            "pages": [],
            "by_engine_kind": [],
            "note": "NOT MEASURED: dry-run validates inputs without extracting pages or calling providers.",
        }

    pages = _page_keys(fields)
    page_text: dict[tuple[str, int, str], str] = {}
    page_stats: list[dict[str, Any]] = []
    for engine_index, engine in enumerate(engines):
        for doc, page in pages:
            path = safe_doc_path(corpus, doc)
            text, seconds, cost = engine.extract(path, page)
            page_text[(doc, page, engine.name)] = text
            usage_rows = getattr(engine, "usages", [])
            usage = usage_rows[-1] if getattr(engine, "name", None) == "gemini" and usage_rows else None
            page_stats.append({"doc": doc, "page": page, "engine": engine.name,
                               "seconds": seconds, "cost_usd": cost,
                               "cost_estimated": bool(usage.estimated) if usage is not None else False,
                               "cached": bool(usage.cached) if usage is not None else False,
                               "model_id": getattr(engine, "model_id", None)})

    scores: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in fields:
        doc, page = str(item["doc"]), int(item["page"] or 1)
        for engine in engines:
            match = match_expected(page_text[(doc, page, engine.name)], item["expected"], item["kind"])
            scores[(engine.name, item["kind"])].append({"id": item["id"], **match})

    summary = []
    for engine_index, engine in enumerate(engines):
        kinds = sorted({str(item["kind"]) for item in fields})
        for kind_index, kind in enumerate(kinds):
            rows = scores[(engine.name, kind)]
            exact = [float(row["exact"]) for row in rows]
            normalized = [float(row["normalized"]) for row in rows]
            summary.append({
                "engine": engine.name, "kind": kind, "n": len(rows),
                "exact_accuracy": _ci(exact, 101 + engine_index * 10 + kind_index),
                "normalized_accuracy": _ci(normalized, 201 + engine_index * 10 + kind_index),
            })

    per_engine_pages = []
    for engine_index, engine in enumerate(engines):
        rows = [r for r in page_stats if r["engine"] == engine.name]
        seconds = [float(r["seconds"]) for r in rows]
        cost = [float(r["cost_usd"]) for r in rows]
        per_engine_pages.append({
            "engine": engine.name, "page_count": len(rows),
            "seconds_per_page": _ci(seconds, 301 + engine_index),
            "usd_per_page": _ci(cost, 401 + engine_index),
        })
    return {"measurement": True, "status": "measured", "field_count": len(fields),
            "page_count": len(pages), "pages": page_stats,
            "by_engine_kind": summary, "by_engine_page": per_engine_pages}


def _markdown(report: dict[str, Any]) -> str:
    lines = ["# Parsing comparison", "", f"Status: `{report['status']}`; measurement: `{str(report['measurement']).lower()}`", ""]
    if not report["measurement"]:
        lines += [report["note"], "", f"Fields: {report['field_count']}; unique pages: {report['page_count']}.", ""]
        return "\n".join(lines)
    lines += ["## Accuracy by engine and field kind", "",
              "| Engine | Kind | N | Exact accuracy (95% CI) | Normalized accuracy (95% CI) |",
              "|---|---|---:|---:|---:|"]
    for row in report["by_engine_kind"]:
        exact, norm = row["exact_accuracy"], row["normalized_accuracy"]
        lines.append(f"| {row['engine']} | {row['kind']} | {row['n']} | "
                     f"{exact['mean']:.3f} [{exact['lo']:.3f}, {exact['hi']:.3f}] | "
                     f"{norm['mean']:.3f} [{norm['lo']:.3f}, {norm['hi']:.3f}] |")
    lines += ["", "## Cost and latency per unique document page", "",
              "| Engine | Pages | Estimated cost pages | USD/page (95% CI) | Seconds/page (95% CI) |",
              "|---|---:|---:|---:|---:|"]
    for row in report["by_engine_page"]:
        cost, seconds = row["usd_per_page"], row["seconds_per_page"]
        estimated_pages = sum(1 for p in report.get("pages", [])
                              if p["engine"] == row["engine"] and p.get("cost_estimated"))
        lines.append(f"| {row['engine']} | {row['page_count']} | {estimated_pages} | ${cost['mean']:.6f} "
                     f"[${cost['lo']:.6f}, ${cost['hi']:.6f}] | {seconds['mean']:.3f} "
                     f"[{seconds['lo']:.3f}, {seconds['hi']:.3f}] |")
    lines += ["", "Normalized accuracy uses whole-page expected-value matching with kind-specific boundaries and "
              "normalization. It does not infer semantic fields. CIs use a deterministic percentile bootstrap.", ""]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    try:
        fields = load_fields(args.fields, args.corpus)
        if not args.dry_run:
            unreviewed = unreviewed_ids(fields)
            if unreviewed:
                print(refusal_message(unreviewed, len(fields), "run parsing comparison"), file=sys.stderr)
                return 2

        selected = list(dict.fromkeys(args.engines))
        engines: list[Any] = []
        cache = None
        if "pdfplumber" in selected:
            engines.append(PdfPlumberEngine())
        if "tesseract" in selected:
            engines.append(TesseractEngine())
        gemini_engine = None
        if "gemini" in selected:
            if args.dry_run:
                # Dry-run deliberately does not construct a client or open a cache.
                engines.append(_NamedEngine("gemini"))
            else:
                cache = DiskCache(args.cache_path)
                gemini_engine = GeminiVisionEngine(cache=cache)
                engines.append(gemini_engine)

        if args.dry_run:
            report = compare_fields(fields, args.corpus, engines, dry_run=True)
        else:
            if gemini_engine is not None:
                page_paths = [(safe_doc_path(args.corpus, doc), page) for doc, page in _page_keys(fields)]
                gemini_engine.preflight(page_paths, spend_cap=args.spend_cap, ledger=Path(args.ledger))
            extraction_error: BaseException | None = None
            try:
                report = compare_fields(fields, args.corpus, engines)
            except BaseException as exc:
                extraction_error = exc
                raise
            finally:
                if gemini_engine is not None and gemini_engine.usages:
                    try:
                        gemini_engine.write_spend(uuid.uuid4().hex, Path(args.ledger))
                    except Exception as ledger_error:
                        detail = f"could not record Gemini spend: {type(ledger_error).__name__}: {ledger_error}"
                        if extraction_error is None:
                            raise RuntimeError(detail) from ledger_error
                        print(f"ERROR: {detail} (while preserving extraction error)", file=sys.stderr)

        out = Path(args.out)
        json_path = out if out.suffix.lower() == ".json" else out.with_suffix(".json")
        md_path = json_path.with_suffix(".md")
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        md_path.write_text(_markdown(report), encoding="utf-8")
        print(f"wrote {md_path} and {json_path}")
        return 0
    except (FieldDatasetError, SpendError, RuntimeError, ValueError, OSError, ImportError, IndexError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if "cache" in locals() and cache is not None:
            cache.close()


def _run(args: argparse.Namespace) -> int:
    return run(args)


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("parse-compare", help="compare text extraction engines against parsing fields")
    parser.add_argument("--fields", required=True, help="field-item JSONL dataset")
    parser.add_argument("--corpus", default="fixtures/corpus", help="document root for safe field paths")
    parser.add_argument("--engines", nargs="+", choices=ENGINE_NAMES, default=["pdfplumber"],
                        help="one or more page extraction engines")
    parser.add_argument("--out", default="benchmarks/runs/parse-compare.json", help="JSON report output path; markdown uses the same stem")
    parser.add_argument("--dry-run", action="store_true", help="validate inputs and write NOT MEASURED output without extracting or calling providers")
    parser.add_argument("--cache-path", default="benchmarks/cache/parsing-gemini.sqlite")
    parser.add_argument("--ledger", default="benchmarks/spend-ledger.jsonl")
    parser.add_argument("--spend-cap", type=float, default=None,
                        help=f"USD cap for ledger total + Gemini estimate; can only lower ${CAP_USD:.2f}")
    parser.set_defaults(func=_run)

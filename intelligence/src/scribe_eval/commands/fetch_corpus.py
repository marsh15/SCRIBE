"""fetch-corpus subcommand (A2: gov-v1 corpus download, pinning and page stats)."""

from __future__ import annotations

import argparse


def _fetch_corpus(args: argparse.Namespace) -> int:
    from ..corpus_fetch import run

    return run(args)


def register(sub: argparse._SubParsersAction) -> None:
    fc = sub.add_parser("fetch-corpus", help="download the gov-v1 corpus, verify sha256, measure pages (A2)")
    fc.add_argument("--manifest", required=True, help="corpus manifest.json (only its URLs are fetched)")
    fc.add_argument("--out", default="fixtures/corpus-gov", help="download directory (git-ignored)")
    fc.add_argument("--pin", action="store_true", help="write missing sha256 / derived records into the manifest")
    fc.add_argument(
        "--rasterize-ocr-layer",
        action="store_true",
        help="write image-only raster copies of old scans that carry a hidden OCR text layer",
    )
    fc.add_argument("--stats", default=None, help="page-stats.json path (default: next to the manifest)")
    fc.set_defaults(func=_fetch_corpus)

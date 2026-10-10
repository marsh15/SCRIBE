#!/usr/bin/env python3
"""Assemble fixtures/corpus-large/ = gold corpus (documents + truth sidecars) + the distractor corpus.

Run (after fixtures/build_distractors.py):
    python3 fixtures/assemble_large_corpus.py [--distractors DIR] [--corpus DIR] [--out DIR] [--skip-verify]

* Copies (never symlinks) every file of fixtures/corpus except the label file FACTS.md, which the baseline harness
  and `scribe-eval ingest-corpus` also never ingest. `*.truth.txt` sidecars ARE copied: both tools skip them as documents,
  and `scribe_eval.corpus.load_document_text` needs them to read the scanned PDF.
* Copies every file of fixtures/distractors, refusing on a file-name collision with a gold file.
* Verifies fixtures/distractors.manifest.json first (sizes and sha256) so a stale or hand-edited directory is not
  silently measured. fixtures/corpus-large/ is git-ignored and is rebuilt from scratch on every run.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "intelligence" / "src"))

from scribe_eval.distractor_check import verify_manifest  # noqa: E402  (stdlib-only helper)

LABEL_FILES = {"FACTS.md"}  # same exclusion as scripts/eval-baseline.ts isCorpusDocument and ingest_corpus.SKIP


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", default=str(ROOT / "corpus"))
    ap.add_argument("--distractors", default=str(ROOT / "distractors"))
    ap.add_argument("--out", default=str(ROOT / "corpus-large"))
    ap.add_argument("--manifest", default=str(ROOT / "distractors.manifest.json"))
    ap.add_argument("--skip-verify", action="store_true", help="do not check the distractor manifest")
    args = ap.parse_args()
    corpus, dist, out = Path(args.corpus), Path(args.distractors), Path(args.out)
    for d in (corpus, dist):
        if not d.is_dir():
            print(f"ERROR: {d} not found" + (" (run fixtures/build_distractors.py first)" if d == dist else ""), file=sys.stderr)
            return 2
    if not args.skip_verify:
        bad = verify_manifest(dist, Path(args.manifest))
        if bad:
            print(f"ERROR: {len(bad)} manifest mismatch(es); rebuild with fixtures/build_distractors.py", file=sys.stderr)
            for v in bad[:20]:
                print("  " + str(v), file=sys.stderr)
            return 1
    gold = sorted(p for p in corpus.iterdir() if p.is_file() and p.name not in LABEL_FILES and not p.name.startswith("."))
    extra = sorted(p for p in dist.iterdir() if p.is_file() and not p.name.startswith("."))
    clash = {p.name.lower() for p in gold} & {p.name.lower() for p in extra}
    if clash:
        print(f"ERROR: file-name collision with gold files: {sorted(clash)}", file=sys.stderr)
        return 1
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for p in gold + extra:
        shutil.copy2(p, out / p.name)
    print(f"{out}: {len(gold)} gold files ({sum(1 for p in gold if not p.name.endswith('.truth.txt'))} documents) "
          f"+ {len(extra)} distractors = {len(gold) + len(extra)} files; excluded {sorted(LABEL_FILES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

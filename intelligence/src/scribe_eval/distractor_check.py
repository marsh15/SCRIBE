"""Verify the distractor corpus against the golden set (Scribe 2.0 evaluation, DESIGN D17).

    uv run --project intelligence python -m scribe_eval.distractor_check \
        --distractors fixtures/distractors --dataset benchmarks/datasets/golden-v1 \
        [--manifest fixtures/distractors.manifest.json]

A distractor document must never change what a golden label means. It is a violation when it:

(a) contains a gold evidence quote (normalized containment, D17), or satisfies a CSV evidence quote
    by the ordered-cells rule of ``scribe_eval.metrics`` (every non-empty cell in order);
(b) contains a ``must_include`` string of an answerable case. Whole-word matching; the exemptions
    are listed in ``MUST_INCLUDE_EXEMPT`` and ``_exempt`` below and are documented in
    docs/scribe2/benchmarks/distractors.md;
(c) contains an exact gold identifier token (``ERR_AUTH_403``, ``INV-2047``, ``NW-T-0417`` ...)
    or matches one of the 15 "deliberately absent topic" patterns (FACTS.md / build_corpus.ABSENT);
(d) has the file name of a gold document (or label sidecar).

It also verifies ``fixtures/distractors.manifest.json`` hashes and prints a summary by format with
an estimate of the legacy (4000/400) and structural chunk counts.

The detection functions are stdlib-only so ``fixtures/build_distractors.py`` (which runs with
``uv run --no-project``) can import them to regenerate a document when it violates a rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import normalize

try:  # the project environment has pydantic; the standalone generator does not
    from .metrics import _cells_in_order, csv_cells
except ImportError:  # pragma: no cover - exercised by the generator, parity-tested in the test suite
    import csv as _csv

    def csv_cells(raw_line: str) -> list[str]:  # type: ignore[misc]
        best: list[str] = []
        for delim in (",", ";", "\t", "|"):
            try:
                row = next(_csv.reader([raw_line], delimiter=delim))
            except (_csv.Error, StopIteration):
                continue
            if len(row) > len(best):
                best = row
        return [c for c in (normalize(c) for c in best) if c]

    def _cells_in_order(cells: list[str], text: str) -> bool:  # type: ignore[misc]
        pos = 0
        for cell in cells:
            found = text.find(cell, pos)
            if found < 0:
                return False
            pos = found + len(cell)
        return bool(cells)


# ----------------------------------------------------------------------------
# Rule data
# ----------------------------------------------------------------------------
# The 15 "deliberately absent topics" (FACTS.md, fixtures/build_corpus.py ABSENT). FACTS.md is also
# parsed at runtime when it is found, so a changed list there is honoured.
ABSENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Stock options, equity or share schemes", r"stock|equity|share scheme|\besop\b|\brsu"),
    ("Contractors / self-employed workers", r"contractor|freelanc|self-employed"),
    ("Sabbaticals and career breaks", r"sabbatical|career break"),
    ("Relocation assistance", r"relocat"),
    ("Salary bands, bonuses and commissions", r"salar|bonus|commission|pay grade|pay band|incentive"),
    ("Pension schemes", r"pension|retirement"),
    ("Remote work and home-office allowances", r"remote work|work from home|home.office|telework|hybrid"),
    ("Dress code or uniform policy", r"dress code|attire|uniform"),
    ("Carbon, emissions or sustainability targets", r"carbon|emission|\bco2\b|sustainab|\besg\b"),
    ("Customs duties and tariff rates", r"tariff|duties|\bduty\b"),
    ("Mergers, acquisitions or IPO plans", r"acquir|acquisition|merger|\bipo\b"),
    ("Smoking policy", r"smok"),
    ("Driver working-time and tachograph rules", r"tachograph|driving time|rest period"),
    ("Whistleblowing channel", r"whistle"),
    ("Maternity leave for the Austin office", r"maternity|\bfmla\b"),
)

# Every identifier that appears in the gold corpus planted facts (FACTS.md). Identifiers found in
# the dataset and, when present, in fixtures/corpus are added at runtime (fleet ids, plates ...).
STATIC_GOLD_IDENTIFIERS: tuple[str, ...] = (
    "BEN-BIK-01", "BEN-CHC-01", "BEN-DEN-01", "BEN-EAP-01", "BEN-GYM-01", "BEN-HLT-01", "BEN-HLT-02",
    "BEN-LIF-01", "BEN-LRN-01", "BEN-MEL-01", "BEN-TRN-01", "BEN-VIS-01",
    "D-ANR", "D-AUS", "D-HAM", "D-LYS", "D-MIL", "D-RTM", "D-WAW",
    "DPA-2026-011", "MSA-2026-0087", "VND-8812", "INC-2025-117", "FSN-2026-014", "SEC-3391",
    "INV-2047", "INV-2051", "INV-2061",
    "ERR_AUTH_401", "ERR_AUTH_403", "ERR_RATE_429", "ERR_SHIP_404", "ERR_VAL_422",
    "EXP-ENT", "EXP-TRV", "FUEL-SUR", "STO-PAL-WK",
    "NW-BOX-L", "NW-BOX-M", "NW-BOX-S", "NW-COLD-04", "NW-GEL-02", "NW-LBL-A6", "NW-PAL-100",
    "NW-PAL-120", "NW-STRAP-PP", "NW-TAPE-48", "NW-WRAP-18",
    "NW-T-0417", "NW-V-0288", "NW-T-0555", "NW-B-0702", "NW-R-0119", "HH-NW", "TX-4471",
    "MI-702-NW", "FR-555-NW", "NL-119-NW",
)

# Identifier shape: upper-case family, then 1-4 dash/underscore separated upper-alnum parts.
IDENTIFIER_RE = re.compile(r"\b[A-Z]{1,6}(?:[-_][A-Z0-9]{1,12}){1,4}\b")

# must_include strings that cannot be enforced as "absent from every distractor":
#  * bare integers (any number of digits) and bare one-decimal numbers (1.3, 16.5): indistinguishable from
#    quantity noise and section numbers (09:00, 2.25, 14,90, 6.5% are NOT bare and stay enforced)
#  * generic words and phrases that any business document contains
#  * the prefix "SEC-": the near-miss design requires neighbouring SEC-nnnn tickets
MUST_INCLUDE_EXEMPT: frozenset[str] = frozenset(
    {"no", "not", "not found", "director", "ceo", "maintenance", "hub", "highest", "entertainment",
     "does not apply", "sec-"}
)

DOC_SUFFIXES = {".pdf", ".docx", ".csv", ".md", ".txt"}
LABEL_FILES = {"FACTS.md"}
MIMES = {
    ".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain", ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass(frozen=True)
class Violation:
    file: str
    rule: str  # a | b | c | d | m (manifest)
    detail: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.file}: {self.detail}"


def _exempt(norm: str) -> bool:
    # bare integers and bare one-decimal numbers (1.3, 16.5) cannot be told apart from section numbers and quantities
    return norm in MUST_INCLUDE_EXEMPT or re.fullmatch(r"\d+(\.\d)?", norm) is not None


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def contains_whole(text: str, needle: str) -> bool:
    """Whole-word / whole-number containment of an already-normalized needle in normalized text."""
    if not needle:
        return False
    start = 0
    n = len(needle)
    while True:
        i = text.find(needle, start)
        if i < 0:
            return False
        before = text[i - 1] if i > 0 else ""
        after = text[i + n] if i + n < len(text) else ""
        ok = True
        if needle[0].isalnum() or needle[0] == "_":
            if before and _is_word_char(before):
                ok = False
            elif before in (".", ",") and i > 1 and text[i - 2].isdigit() and needle[0].isdigit():
                ok = False  # tail of a longer number such as 21.3 or 1,840
        if ok and (needle[-1].isalnum() or needle[-1] == "_"):
            if after and _is_word_char(after):
                ok = False
            elif after in (".", ",") and i + n + 1 < len(text) and text[i + n + 1].isdigit() and needle[-1].isdigit():
                ok = False  # head of a longer number such as 1.35 or 1,840
        if ok:
            return True
        start = i + 1


def contains_token(text_lower: str, token_lower: str) -> bool:
    """Identifier token containment: not embedded in a longer dash/underscore/alnum token."""
    start = 0
    n = len(token_lower)
    while True:
        i = text_lower.find(token_lower, start)
        if i < 0:
            return False
        before = text_lower[i - 1] if i > 0 else ""
        after = text_lower[i + n] if i + n < len(text_lower) else ""
        if not (before and (before.isalnum() or before in "_-")) and not (after and (after.isalnum() or after in "_-")):
            return True
        start = i + 1


@dataclass
class Gold:
    quotes: list[tuple[str, str]] = field(default_factory=list)  # (case/doc label, normalized quote)
    csv_quotes: list[tuple[str, list[str]]] = field(default_factory=list)  # (raw quote, cells)
    must_include: list[tuple[str, str]] = field(default_factory=list)  # (case id, normalized)
    identifiers: set[str] = field(default_factory=set)  # lower-case tokens
    absent: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    doc_names: set[str] = field(default_factory=set)  # lower-case file names
    _mi_active: list[tuple[str, str]] = field(default_factory=list)

    # ---- construction --------------------------------------------------------------------
    @classmethod
    def load(cls, dataset_dir: Path, corpus_dir: Path | None = None) -> "Gold":
        g = cls()
        dataset_dir = Path(dataset_dir)
        cases = [json.loads(ln) for ln in (dataset_dir / "cases.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
        id_sources: list[str] = []
        for c in cases:
            id_sources += [c.get("question", ""), c.get("reference_answer", "")]
            for e in c.get("evidence", []):
                q = e["quote"]
                g.quotes.append((f"{c['id']}:{e['doc']}", normalize(q)))
                if e["doc"].lower().endswith(".csv"):
                    g.csv_quotes.append((q, csv_cells(q)))
                g.doc_names.add(e["doc"].lower())
                id_sources.append(q)
            for f in c.get("forbidden", []):
                g.doc_names.add(f["doc"].lower())
            if c.get("answerable"):
                for m in c.get("must_include", []):
                    nm = normalize(m)
                    id_sources.append(m)
                    if nm and not _exempt(nm):
                        g.must_include.append((c["id"], nm))
        g.must_include = sorted(set(g.must_include), key=lambda t: (t[1], t[0]))
        g._mi_active = g.must_include
        g.identifiers = {t.lower() for t in STATIC_GOLD_IDENTIFIERS}
        for s in id_sources:
            g.identifiers.update(t.lower() for t in IDENTIFIER_RE.findall(s))
        patterns = list(ABSENT_PATTERNS)
        corpus_dir = Path(corpus_dir) if corpus_dir else None
        if corpus_dir and corpus_dir.is_dir():
            for p in sorted(corpus_dir.iterdir()):
                if p.is_file() and not p.name.startswith("."):
                    g.doc_names.add(p.name.lower())
                    if p.suffix.lower() in (".csv", ".md", ".txt"):
                        txt = p.read_bytes().decode("utf-8-sig", errors="replace")
                        g.identifiers.update(t.lower() for t in IDENTIFIER_RE.findall(txt))
            facts = corpus_dir / "FACTS.md"
            if facts.is_file():
                for m in re.finditer(r"\(pattern: `([^`]+)`\)", facts.read_text(encoding="utf-8")):
                    if m.group(1) not in {p for _, p in patterns}:
                        patterns.append(("FACTS.md pattern", m.group(1)))
        g.doc_names.update(n.lower() for n in LABEL_FILES)
        g.absent = [(label, re.compile(rx)) for label, rx in patterns]
        return g

    # ---- detection -----------------------------------------------------------------------
    def check_text(self, text: str) -> list[tuple[str, str]]:
        """Return (rule, detail) for every violation in a document's plain text."""
        out: list[tuple[str, str]] = []
        norm = normalize(text)
        lower = text.lower()
        for label, q in self.quotes:
            if q and q in norm:
                out.append(("a", f"gold quote of {label}: {q[:70]!r}"))
        for raw, cells in self.csv_quotes:
            if _cells_in_order(cells, norm):
                out.append(("a", f"CSV ordered-cells of gold record: {raw[:70]!r}"))
        for cid, m in self._mi_active:
            if contains_whole(norm, m):
                out.append(("b", f"must_include of {cid}: {m!r}"))
        for ident in sorted(self.identifiers):
            if contains_token(lower, ident):
                out.append(("c", f"gold identifier {ident.upper()}"))
        for label, rx in self.absent:
            m = rx.search(lower)
            if m:
                out.append(("c", f"absent-topic pattern ({label}): {m.group(0)!r}"))
        return out

    def phrase_ok(self, s: str) -> bool:
        """Cheap pre-filter for generated fragments (rules b and c; quotes are whole sentences)."""
        norm = normalize(s)
        lower = s.lower()
        for _, m in self._mi_active:
            if m in norm and contains_whole(norm, m):
                return False
        for ident in self.identifiers:
            if ident in lower and contains_token(lower, ident):
                return False
        return True

    def check_name(self, name: str) -> list[tuple[str, str]]:
        low = name.lower()
        if low in self.doc_names or low.endswith(".truth.txt"):
            return [("d", f"file name collides with a gold/label file: {name}")]
        return []


# ----------------------------------------------------------------------------
# Text extraction (what the evaluator would see)
# ----------------------------------------------------------------------------
def extract_text(path: Path) -> str:
    from .corpus import load_document_text

    return load_document_text(path)


def legacy_text(path: Path) -> str:
    """Approximation of the legacy TS extractor: CSV rows are joined with spaces."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        import csv
        import io

        raw = path.read_bytes().decode("utf-8-sig", errors="replace")
        first = raw.splitlines()[0] if raw.strip() else ""
        delim = max((",", ";", "\t", "|"), key=lambda d: first.count(d))
        rows = list(csv.reader(io.StringIO(raw), delimiter=delim))
        return "\n".join(" ".join(r) for r in rows)
    return extract_text(path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def list_documents(directory: Path) -> list[Path]:
    return sorted(p for p in Path(directory).iterdir() if p.is_file() and p.suffix.lower() in DOC_SUFFIXES and not p.name.startswith("."))


# ----------------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------------
def check_directory(directory: Path, gold: Gold, *, extractor=extract_text) -> list[Violation]:
    violations: list[Violation] = []
    directory = Path(directory)
    for p in sorted(x for x in directory.iterdir() if x.is_file() and not x.name.startswith(".")):
        for rule, detail in gold.check_name(p.name):
            violations.append(Violation(p.name, rule, detail))
        if p.suffix.lower() not in DOC_SUFFIXES:
            continue
        try:
            text = extractor(p)
        except Exception as exc:  # unreadable file: report, do not crash the whole run
            violations.append(Violation(p.name, "x", f"cannot extract text: {type(exc).__name__}: {exc}"))
            continue
        for rule, detail in gold.check_text(text):
            violations.append(Violation(p.name, rule, detail))
    return violations


def verify_manifest(directory: Path, manifest_path: Path) -> list[Violation]:
    directory = Path(directory)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    out: list[Violation] = []
    listed = {f["name"]: f for f in manifest.get("files", [])}
    present = {p.name for p in directory.iterdir() if p.is_file() and not p.name.startswith(".")}
    for name in sorted(set(listed) - present):
        out.append(Violation(name, "m", "listed in the manifest but missing on disk"))
    for name in sorted(present - set(listed)):
        out.append(Violation(name, "m", "present on disk but not in the manifest"))
    for name in sorted(set(listed) & present):
        p = directory / name
        want = listed[name]
        if p.stat().st_size != want.get("size") or sha256_file(p) != want.get("sha256"):
            out.append(Violation(name, "m", "size/sha256 differs from the manifest (binary PDF/DOCX bytes can "
                                            "differ across reportlab/python-docx versions; see manifest 'environment')"))
    return out


# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------
def _legacy_chunk_count(text: str) -> tuple[int, bool]:
    try:
        from scribe_ingest.legacy_chunker import chunk_content

        return len(chunk_content(text)), True
    except Exception:
        return max(1, math.ceil(len(text.strip()) / 3600)), False  # 4000 chars less overlap, char-based


def _structural_counts(path: Path) -> tuple[int, int] | None:
    try:
        from scribe_ingest import parse
        from scribe_ingest.chunking import chunk_document

        doc = parse(path.read_bytes(), MIMES[path.suffix.lower()], name=path.name)
        cs = chunk_document(doc)
        return len(cs.children), len(cs.parents)
    except Exception:
        return None


def summarize(directory: Path, *, sample: int = 40, out=None, chunks: bool = True) -> dict:
    out = out or sys.stdout
    docs = list_documents(directory)
    by_fmt: dict[str, list[Path]] = {}
    for p in docs:
        by_fmt.setdefault(p.suffix.lower().lstrip("."), []).append(p)
    report: dict = {"formats": {}, "total_docs": len(docs)}
    total_kb = sum(p.stat().st_size for p in docs) / 1024
    print(f"{len(docs)} documents, {total_kb / 1024:.2f} MB total", file=out)
    legacy_total = 0
    legacy_exact = True
    t0 = time.time()
    struct_total = 0.0
    struct_note = ""
    for fmt in sorted(by_fmt):
        ps = by_fmt[fmt]
        kb = sum(p.stat().st_size for p in ps) / 1024
        row = {"docs": len(ps), "kb": round(kb, 1)}
        if chunks:
            n_legacy = 0
            for p in ps:
                try:
                    n, exact = _legacy_chunk_count(legacy_text(p))
                except Exception:
                    n, exact = 0, False
                n_legacy += n
                legacy_exact = legacy_exact and exact
            row["legacy_chunks"] = n_legacy
            legacy_total += n_legacy
            step = max(1, math.ceil(len(ps) * len(by_fmt) / max(1, sample)))
            picked = ps[::step][: max(1, sample // len(by_fmt))]
            counts = [c for c in (_structural_counts(p) for p in picked) if c is not None]
            if counts:
                per_doc = sum(c[0] for c in counts) / len(counts)
                row["structural_child_chunks_est"] = round(per_doc * len(ps))
                row["structural_sampled_docs"] = len(counts)
                struct_total += per_doc * len(ps)
            else:
                struct_note = " (scribe_ingest not importable: no structural estimate)"
        report["formats"][fmt] = row
    print(f"{'format':<6} {'docs':>5} {'KB':>10} {'legacy 4000/400 chunks':>24} {'structural child chunks (est.)':>32}", file=out)
    for fmt, row in report["formats"].items():
        print(f"{fmt:<6} {row['docs']:>5} {row['kb']:>10.1f} {row.get('legacy_chunks', '-'):>24} "
              f"{row.get('structural_child_chunks_est', '-'):>32}", file=out)
    if chunks:
        how = "legacy_chunker port" if legacy_exact else "char-based estimate (chars/3600)"
        print(f"TOTAL legacy chunks: {legacy_total} ({how}); structural child chunks: ~{round(struct_total)}"
              f" (ESTIMATE from a sample of <= {sample} docs scaled up per format){struct_note}; "
              f"took {time.time() - t0:.0f}s", file=out)
        report["legacy_total"] = legacy_total
        report["structural_total_est"] = round(struct_total)
    return report


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scribe_eval.distractor_check", description=__doc__.split("\n\n")[0])
    ap.add_argument("--distractors", default="fixtures/distractors")
    ap.add_argument("--dataset", default="benchmarks/datasets/golden-v1")
    ap.add_argument("--gold-corpus", default="fixtures/corpus", help="gold corpus dir (name list, identifiers, FACTS.md patterns); optional")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--sample", type=int, default=40, help="docs parsed for the structural chunk estimate")
    ap.add_argument("--no-chunks", action="store_true", help="skip chunk-count estimates")
    args = ap.parse_args(argv)

    directory = Path(args.distractors)
    if not directory.is_dir():
        print(f"ERROR: distractor directory not found: {directory}", file=sys.stderr)
        return 2
    gold_corpus = Path(args.gold_corpus) if args.gold_corpus else None
    gold = Gold.load(Path(args.dataset), gold_corpus if gold_corpus and gold_corpus.is_dir() else None)
    print(f"gold: {len(gold.quotes)} evidence quotes ({len(gold.csv_quotes)} CSV), {len(gold.must_include)} enforced must_include strings, "
          f"{len(gold.identifiers)} identifiers, {len(gold.absent)} absent-topic patterns, {len(gold.doc_names)} protected file names")
    violations = check_directory(directory, gold)
    if args.manifest:
        mp = Path(args.manifest)
        if mp.is_file():
            violations += verify_manifest(directory, mp)
        else:
            violations.append(Violation(str(mp), "m", "manifest file not found"))
    summarize(directory, sample=args.sample, chunks=not args.no_chunks)
    if violations:
        print(f"\nFAIL: {len(violations)} violation(s)", file=sys.stderr)
        for v in violations[:200]:
            print("  " + str(v), file=sys.stderr)
        if len(violations) > 200:
            print(f"  ... and {len(violations) - 200} more", file=sys.stderr)
        return 1
    print("\nOK: no distractor leaks a gold quote, must_include string, gold identifier, absent-topic keyword or gold file name"
          + ("; manifest verified" if args.manifest else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

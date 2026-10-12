"""Dataset validation (all checks return human-readable error strings)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .corpus import load_document_text
from .dataset import SPLITS, Case, parse_cases
from .normalize import normalize

DEFAULT_RATIOS = {"dev": 0.4, "validation": 0.2, "test": 0.4}
RATIO_TOLERANCE = 0.05
MIN_CASES_FOR_SPLIT_COVERAGE = 5
MAX_BALANCE_FACTOR = 2


def validate_dataset(dataset_dir: Path, corpus_dir: Path) -> tuple[list[Case], list[str]]:
    errors: list[str] = []
    cases_path = dataset_dir / "cases.jsonl"
    if not cases_path.is_file():
        return [], [f"missing {cases_path}"]
    cases, parse_errors = parse_cases(cases_path)
    errors += parse_errors

    ratios = dict(DEFAULT_RATIOS)
    meta_path = dataset_dir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta.get("split_ratios"), dict):
                ratios = {k: float(v) for k, v in meta["split_ratios"].items()}
                total = sum(ratios.values())
                if total > 1.5:  # given as percentages
                    ratios = {k: v / total for k, v in ratios.items()}
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            errors.append(f"meta.json: invalid: {exc}")

    seen: Counter[str] = Counter(c.id for c in cases)
    for cid, n in seen.items():
        if n > 1:
            errors.append(f"[{cid}] duplicate id ({n} occurrences)")

    texts: dict[str, str | None] = {}

    def doc_text(name: str) -> str | None:
        if name not in texts:
            p = corpus_dir / name
            if not p.is_file():
                texts[name] = None
            else:
                try:
                    texts[name] = normalize(load_document_text(p))
                except Exception as exc:  # unreadable document
                    errors.append(f"document {name}: cannot read: {exc}")
                    texts[name] = None
        return texts[name]

    for c in cases:
        if c.answerable and not c.evidence:
            errors.append(f"[{c.id}] answerable case has no evidence")
        if not c.answerable and c.evidence:
            errors.append(f"[{c.id}] unanswerable case must have empty evidence")
        for i, ev in enumerate(c.evidence):
            tag = f"[{c.id}] evidence[{i}] ({ev.doc})"
            if len(ev.quote) > 200:
                errors.append(f"{tag}: quote is {len(ev.quote)} chars (max 200)")
            if not (corpus_dir / ev.doc).is_file():
                errors.append(f"{tag}: document not found in corpus {corpus_dir}")
                continue
            text = doc_text(ev.doc)
            if text is None:
                continue
            q = normalize(ev.quote)
            if not q:
                errors.append(f"{tag}: quote is empty after normalization")
                continue
            n = text.count(q)
            if n == 0:
                errors.append(f"{tag}: quote found 0 times (must be exactly once): {ev.quote[:60]!r}")
            elif n > 1:
                errors.append(f"{tag}: quote found {n} times (must be exactly once): {ev.quote[:60]!r}")
        for fb in c.forbidden:
            if not (corpus_dir / fb.doc).is_file():
                errors.append(f"[{c.id}] forbidden doc not found in corpus: {fb.doc}")

    errors += _check_splits(cases, ratios)
    errors += _check_balance(cases)
    return cases, errors


def _check_splits(cases: list[Case], ratios: dict[str, float]) -> list[str]:
    errs: list[str] = []
    if not cases:
        return errs
    total = len(cases)
    counts = Counter(c.split for c in cases)
    for s in SPLITS:
        actual = counts[s] / total
        want = ratios.get(s, 0.0)
        if abs(actual - want) > RATIO_TOLERANCE + 1e-9:
            errs.append(
                f"split '{s}' is {actual:.1%} of cases ({counts[s]}/{total}); "
                f"expected {want:.0%} +-{RATIO_TOLERANCE:.0%}"
            )
    by_cat: dict[str, Counter[str]] = defaultdict(Counter)
    for c in cases:
        by_cat[c.category][c.split] += 1
    for cat, cnt in sorted(by_cat.items()):
        if sum(cnt.values()) >= MIN_CASES_FOR_SPLIT_COVERAGE:
            missing = [s for s in SPLITS if cnt[s] == 0]
            if missing:
                errs.append(f"category '{cat}' missing from split(s): {', '.join(missing)}")
    return errs


def _check_balance(cases: list[Case]) -> list[str]:
    sizes = Counter(c.category for c in cases)
    if len(sizes) < 2:
        return []
    small_cat, small = min(sizes.items(), key=lambda kv: kv[1])
    return [
        f"category '{cat}' has {n} cases, more than {MAX_BALANCE_FACTOR}x the smallest "
        f"('{small_cat}' with {small})"
        for cat, n in sorted(sizes.items())
        if n > MAX_BALANCE_FACTOR * small
    ]


def summary_table(cases: list[Case]) -> str:
    cats = sorted({c.category for c in cases})
    grid: dict[str, Counter[str]] = {k: Counter() for k in cats}
    ans: Counter[str] = Counter()
    for c in cases:
        grid[c.category][c.split] += 1
        ans[c.category] += c.answerable
    header = ["category", *SPLITS, "total", "answerable"]
    rows = [header]
    for k in cats:
        rows.append([k, *(str(grid[k][s]) for s in SPLITS), str(sum(grid[k].values())), str(ans[k])])
    tot = Counter(c.split for c in cases)
    rows.append(["TOTAL", *(str(tot[s]) for s in SPLITS), str(len(cases)), str(sum(ans.values()))])
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    return "\n".join("  ".join(v.ljust(widths[i]) for i, v in enumerate(r)) for r in rows)

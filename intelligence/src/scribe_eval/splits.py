"""Deterministic stratified split assignment."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path


def assign_splits(rows: list[dict], seed: int, only_origin: str | None = None) -> list[dict]:
    """Assign dev/validation/test 40/20/40 per category (remainder to dev).

    ``only_origin``: assign ONLY rows whose ``origin`` equals it (T28 growth: the 120 existing cases keep their
    splits, so nothing already seen is moved). The shuffle seed then also includes the origin, so the original
    assignment is unaffected and the new batch is stratified on its own.
    """
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if only_origin is None or r.get("origin") == only_origin:
            by_cat[r["category"]].append(r)
    salt = "" if only_origin is None else f":{only_origin}"
    for cat in sorted(by_cat):
        group = sorted(by_cat[cat], key=lambda r: r["id"])
        random.Random(f"{seed}:{cat}{salt}").shuffle(group)
        n = len(group)
        n_val = round(n * 0.2)
        n_test = round(n * 0.4)
        n_dev = n - n_val - n_test  # remainder to dev
        for i, r in enumerate(group):
            r["split"] = "dev" if i < n_dev else "validation" if i < n_dev + n_val else "test"
    return rows


def assign_splits_file(dataset_dir: Path, seed: int, only_origin: str | None = None) -> int:
    path = dataset_dir / "cases.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assign_splits(rows, seed, only_origin)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return len(rows)

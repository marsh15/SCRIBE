"""Deterministic stratified split assignment."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path


def assign_splits(rows: list[dict], seed: int) -> list[dict]:
    """Assign dev/validation/test 40/20/40 per category (remainder to dev)."""
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    for cat in sorted(by_cat):
        group = sorted(by_cat[cat], key=lambda r: r["id"])
        random.Random(f"{seed}:{cat}").shuffle(group)
        n = len(group)
        n_val = round(n * 0.2)
        n_test = round(n * 0.4)
        n_dev = n - n_val - n_test  # remainder to dev
        for i, r in enumerate(group):
            r["split"] = "dev" if i < n_dev else "validation" if i < n_dev + n_val else "test"
    return rows


def assign_splits_file(dataset_dir: Path, seed: int) -> int:
    path = dataset_dir / "cases.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assign_splits(rows, seed)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return len(rows)

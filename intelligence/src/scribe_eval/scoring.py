"""Score a rankings JSONL file against a dataset split."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .dataset import Case, load_cases
from .metrics import CaseResult, evaluate_case, summarize


class ScoreError(Exception):
    pass


def load_rankings(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            cid = rec["case_id"]
            if not isinstance(rec.get("ranking"), list):
                raise ValueError("'ranking' must be a list")
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise ScoreError(f"{path}:{n}: bad rankings line: {exc!r}") from exc
        if cid in out:
            raise ScoreError(f"{path}:{n}: duplicate case_id {cid}")
        out[cid] = rec
    return out


def dataset_version(dataset_dir: Path) -> str:
    meta = dataset_dir / "meta.json"
    if meta.is_file():
        return str(json.loads(meta.read_text(encoding="utf-8")).get("version", dataset_dir.name))
    return dataset_dir.name


def score_split(dataset_dir: Path, split: str, rankings_path: Path) -> tuple[list[Case], list[CaseResult], dict[str, Any]]:
    cases = [c for c in load_cases(dataset_dir / "cases.jsonl") if c.split == split]
    if not cases:
        raise ScoreError(f"no cases in split '{split}' of {dataset_dir}")
    rankings = load_rankings(rankings_path)
    missing = [c.id for c in cases if c.id not in rankings]
    if missing:
        raise ScoreError(f"rankings file is missing {len(missing)} case(s) of split '{split}': {', '.join(missing)}")
    results = [evaluate_case(c, rankings[c.id]) for c in cases]
    return cases, results, summarize(results)


def format_table(summary: dict[str, Any]) -> str:
    cols = ["recall@1", "recall@5", "recall@10", "mrr", "ndcg@10", "precision@5"]
    lines = [f"{'category':<22}{'n':>4} " + " ".join(f"{c:>10}" for c in cols)]

    def row(label: str, agg: Any) -> str:
        return f"{label:<22}{agg.n:>4} " + " ".join(f"{agg.mean.get(c, float('nan')):>10.3f}" for c in cols)

    for cat, agg in summary["per_category"].items():
        lines.append(row(cat, agg))
    lines.append("-" * len(lines[0]))
    lines.append(row("OVERALL", summary["overall"]))
    ov = summary["overall"]
    lines.append("95% CI (overall): " + ", ".join(
        f"{c} [{ov.ci95[c][0]:.3f}, {ov.ci95[c][1]:.3f}]" for c in cols if c in ov.ci95))
    u = summary["unanswerable"]
    lines.append(f"unanswerable (excluded): {u['count']}, max top score: {u['max_top_score']}")
    return "\n".join(lines)


def summary_to_json(summary: dict[str, Any]) -> dict[str, Any]:
    def agg(a: Any) -> dict[str, Any]:
        return {"n": a.n, "mean": a.mean, "ci95": {k: list(v) for k, v in a.ci95.items()}}

    return {
        "overall": agg(summary["overall"]),
        "per_category": {c: agg(a) for c, a in summary["per_category"].items()},
        "unanswerable": summary["unanswerable"],
    }

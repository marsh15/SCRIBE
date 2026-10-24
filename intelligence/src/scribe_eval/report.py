"""Aggregate persisted retrieval and answer measurements into dataset-separated reports."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .metrics import bootstrap_ci_mean
from .runs import load_run

COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("Recall@5", "recall@5", "mean"),
    ("Recall@10", "recall@10", "mean"),
    ("MRR", "mrr", "mean"),
    ("nDCG@10", "ndcg@10", "mean"),
    ("Citation validity", "citation_validity", "mean"),
    ("Groundedness", "groundedness", "mean"),
    ("Abstention accuracy", "abstention_accuracy", "mean"),
    ("Leak rate", "leak_rate", "mean"),
    ("p95 latency", "latency_ms", "p95"),
    ("$/query", "cost_usd", "mean"),
)
FAKE_STAMPS = {"FAKE - NOT A MEASUREMENT", "SEEDED - NOT A MEASUREMENT"}


class ReportError(ValueError):
    """Invalid, unsafe, or ambiguous report inputs."""


def dataset_sha256(dataset_dir: str | Path) -> str:
    return hashlib.sha256((Path(dataset_dir) / "cases.jsonl").read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_kind(run_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    has_retrieval = (run_dir / "results.jsonl").is_file()
    has_answers = (run_dir / "answers.jsonl").is_file()
    if has_answers:
        answer_rows = _load_jsonl(run_dir / "answers.jsonl")
        if has_retrieval:
            retrieval_rows = _load_jsonl(run_dir / "results.jsonl")
            if retrieval_rows:
                raise ReportError(f"{run_dir}: contains both retrieval results and answer rows")
        return "answer", answer_rows
    if has_retrieval:
        return "retrieval", _load_jsonl(run_dir / "results.jsonl")
    raise ReportError(f"{run_dir}: expected results.jsonl or answers.jsonl")


def _validate_run(run: dict[str, Any], rows: list[dict[str, Any]], path: Path) -> None:
    if run.get("measurement") is not True:
        raise ReportError(f"{path}: refuses run with measurement != true")
    stamp = str(run.get("stamp") or "").upper()
    if stamp in FAKE_STAMPS or "FAKE" in stamp or "SEEDED" in stamp or run.get("fake_components"):
        raise ReportError(f"{path}: refuses fake or seeded run")
    if run.get("seeded") is True or run.get("seeded_baseline") is True:
        raise ReportError(f"{path}: refuses seeded run")
    if not run.get("run_id") or not run.get("dataset_sha256") or not run.get("config_hash"):
        raise ReportError(f"{path}: run is missing run id, dataset sha256, or profile config hash")
    model_ids = run.get("model_ids") or {}
    if not isinstance(model_ids, dict) or not model_ids:
        raise ReportError(f"{path}: model ids are missing")
    fake_models = [str(model).lower() for model in model_ids.values()
                   if any(token in str(model).lower() for token in ("fake", "mock", "stub"))]
    if fake_models:
        raise ReportError(f"{path}: refuses fake model id(s): {', '.join(fake_models)}")
    review = (run.get("summary") or {}).get("review") or run.get("review")
    if isinstance(review, dict):
        review_status = str(review.get("status", "")).lower()
        if review.get("unreviewed_in_split", 0) or review_status in {"seeded", "unreviewed", "not yet human-reviewed"}:
            raise ReportError(f"{path}: run review metadata records unreviewed or seeded cases")
    if not rows:
        raise ReportError(f"{path}: run contains no persisted case rows")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str) or not row["case_id"]:
            raise ReportError(f"{path}: a persisted row has no case_id")


def _dedupe_rows(rows: Iterable[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = row["case_id"]
        if case_id in seen:
            if seen[case_id] != row:
                raise ReportError(f"{path}: conflicting duplicate rows for case {case_id}")
            continue
        seen[case_id] = row
    return list(seen.values())


def _p95(values: Sequence[float]) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _bootstrap_ci(
    values: Sequence[float], statistic: Callable[[Sequence[float]], float], *, seed: int, n_resamples: int = 10_000,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap for a statistic; p95 uses this to bootstrap p95 itself."""
    n = len(values)
    if not n:
        return math.nan, math.nan
    rng = random.Random(seed)
    samples = sorted(statistic([values[rng.randrange(n)] for _ in range(n)]) for _ in range(n_resamples))
    return samples[int(alpha * n_resamples / 2)], samples[min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))]


def _seed(label: str) -> int:
    return int.from_bytes(hashlib.sha256(label.encode()).digest()[:4], "big")


def _metric_value(kind: str, row: dict[str, Any], metric: str) -> float | None:
    if metric == "leak_rate":
        leak = row.get("leak")
        if not isinstance(leak, dict) or not isinstance(leak.get("leaked"), bool):
            return None
        if leak["leaked"]:
            return 1.0
        if row.get("_leak_rate_verified") is not True or leak.get("provenance_missing_items", 0):
            return None
        return 0.0
    if metric == "latency_ms":
        value = row.get("latency_ms")
    elif metric == "cost_usd":
        cost = row.get("cost")
        value = cost.get("usd") if isinstance(cost, dict) else None
    else:
        metrics = row.get("metrics")
        value = metrics.get(metric) if isinstance(metrics, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def _combined_rows(retrieval: dict[str, Any] | None, answer: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Join complementary persisted per-case rows; never infer unavailable components."""
    retrieval_rows = {r["case_id"]: {**r, "_leak_rate_verified": retrieval["run"].get("leak_rate_verified")}
                      for r in retrieval["rows"]} if retrieval else {}
    answer_rows = {r["case_id"]: {**r, "_leak_rate_verified": answer["run"].get("leak_rate_verified")}
                   for r in answer["rows"]} if answer else {}
    ids = sorted(set(retrieval_rows) | set(answer_rows))
    # Metrics remain source-local. A row with only one source can still contribute its recorded metrics.
    out = []
    for case_id in ids:
        rr, ar = retrieval_rows.get(case_id), answer_rows.get(case_id)
        category = (rr or ar or {}).get("_dataset_category") or (rr or ar or {}).get("category") or "uncategorized"
        row: dict[str, Any] = {"case_id": case_id, "category": category, "sources": {}}
        if rr:
            row["sources"]["retrieval"] = rr
        if ar:
            row["sources"]["answer"] = ar
        out.append(row)
    return out


def _value_for_report(row: dict[str, Any], metric: str) -> float | None:
    sources = row["sources"]
    if metric in {"recall@5", "recall@10", "mrr", "ndcg@10"}:
        return _metric_value("retrieval", sources["retrieval"], metric) if "retrieval" in sources else None
    if metric in {"citation_validity", "groundedness", "abstention_accuracy"}:
        return _metric_value("answer", sources["answer"], metric) if "answer" in sources else None
    if metric == "leak_rate":
        available = [_metric_value(k, v, metric) for k, v in sources.items()]
        # A positive leak verdict is sufficient evidence. A zero requires every available source
        # to have complete provenance and a verified zero verdict.
        if any(value == 1.0 for value in available):
            return 1.0
        if available and all(value == 0.0 for value in available):
            return 0.0
        return None
    if metric == "latency_ms":
        values = [_metric_value(k, v, metric) for k, v in sources.items()]
        if len(sources) == 2:
            return sum(values) if all(v is not None for v in values) else None
        return values[0] if values and values[0] is not None else None
    if metric == "cost_usd":
        # For paired runs, a total is only available when both persisted costs are available.
        values = [_metric_value(k, v, metric) for k, v in sources.items()]
        if len(sources) == 2:
            return sum(values) if all(v is not None for v in values) else None
        return values[0] if values and values[0] is not None else None
    return None


def _format_cell(values: Sequence[float], metric: str, seed_label: str) -> str:
    if not values:
        return "n/a (NOT RECORDED)"
    if metric == "p95":
        center = _p95(values)
        lo, hi = _bootstrap_ci(values, _p95, seed=_seed(seed_label))
    else:
        center = sum(values) / len(values)
        # Keep the existing mean-bootstrap helper as the canonical CI implementation.
        lo, hi = bootstrap_ci_mean(values, seed=_seed(seed_label))
    return f"{center:.3f} [{lo:.3f}, {hi:.3f}] (n={len(values)})"


def _markdown_table(dataset_label: str, sha: str, groups: dict[tuple[str, str], list[dict[str, Any]]],
                    run_infos: list[dict[str, Any]]) -> str:
    profiles = sorted({profile for _, profile in groups})
    headers = ["Category × profile", *(label for label, _, _ in COLUMNS)]
    lines = [f"## Dataset: {dataset_label}", "", f"Dataset sha256: `{sha}`", "",
             "| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for profile in profiles:
        category_names = sorted(category for category, p in groups if p == profile)
        for category in category_names:
            rows = groups[(category, profile)]
            cells = [f"{category} × {profile}"]
            for _, metric, stat in COLUMNS:
                values = [v for row in rows if (v := _value_for_report(row, metric)) is not None]
                if metric == "leak_rate" and len(values) < len(rows):
                    values = []  # leak rate needs a verified verdict for every case in the group
                cell = _format_cell(values, stat, f"{sha}:{profile}:{category}:{metric}")
                if metric in {"latency_ms", "cost_usd"} and values:
                    kinds = {kind for row in rows for kind in row["sources"]}
                    if kinds == {"retrieval"}:
                        cell = cell.replace(f"(n={len(values)})", f"(n={len(values)}; retrieval only, partial)")
                    elif kinds == {"answer"}:
                        cell = cell.replace(f"(n={len(values)})", f"(n={len(values)}; answer only, partial)")
                    if len(values) < len(rows):
                        cell = cell.replace(")", "; incomplete case-stage data excluded)")
                cells.append(cell)
            lines.append("| " + " | ".join(cells) + " |")
        overall = [row for category in category_names for row in groups[(category, profile)]]
        cells = [f"Overall × {profile}"]
        for _, metric, stat in COLUMNS:
            values = [v for row in overall if (v := _value_for_report(row, metric)) is not None]
            if metric == "leak_rate" and len(values) < len(overall):
                values = []
            cell = _format_cell(values, stat, f"{sha}:{profile}:overall:{metric}")
            if metric in {"latency_ms", "cost_usd"} and values:
                kinds = {kind for row in overall for kind in row["sources"]}
                if kinds == {"retrieval"}:
                    cell = cell.replace(f"(n={len(values)})", f"(n={len(values)}; retrieval only, partial)")
                elif kinds == {"answer"}:
                    cell = cell.replace(f"(n={len(values)})", f"(n={len(values)}; answer only, partial)")
                if len(values) < len(overall):
                    cell = cell.replace(")", "; incomplete case-stage data excluded)")
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(["", "Values are means with 95% bootstrap CIs, except latency uses the p95 statistic and its bootstrap CI. `n` is contributing cases.", "", "Stage coverage:"])
    for profile in profiles:
        kinds = sorted({info["kind"] for info in run_infos if info["run"].get("profile_name", "unnamed") == profile})
        answer_stage = "answer evaluation (generation + verification + semantic judge)"
        if kinds == ["answer", "retrieval"]:
            coverage = f"retrieval + {answer_stage} (end-to-end evaluation pipeline)"
        elif kinds == ["answer"]:
            coverage = f"{answer_stage} only (partial; not end-to-end)"
        else:
            coverage = "retrieval only (partial; not end-to-end)"
        lines.append(f"- profile `{profile}`: {coverage}")
    lines.extend(["", "Run provenance:"])
    for info in sorted(run_infos, key=lambda x: x["run"].get("run_id", "")):
        run = info["run"]
        config_detail = (f"answer config hash `{run['config_hash']}`; retrieval profile config hash "
                         f"`{run.get('retrieval_config_hash') or 'NOT RECORDED'}`"
                         if info["kind"] == "answer" else f"profile config hash `{run['config_hash']}`")
        lines.append(
            f"- run `{run['run_id']}`; dataset sha256 `{run['dataset_sha256']}`; profile `{run.get('profile_name', 'unknown')}`; "
            f"{config_detail}; git commit `{run.get('code_commit') or 'NOT RECORDED'}`; "
            f"models `{json.dumps(run.get('model_ids', {}), sort_keys=True)}`"
        )
    return "\n".join(lines)


def build_report(run_dirs: Sequence[str | Path], dataset_dirs: Sequence[str | Path]) -> str:
    if not run_dirs:
        raise ReportError("provide at least one run directory")
    if not dataset_dirs:
        raise ReportError("provide at least one --dataset directory to verify dataset hashes and case reviews")
    datasets_by_sha: dict[str, Path] = {}
    for value in dataset_dirs:
        path = Path(value)
        try:
            sha = dataset_sha256(path)
        except OSError as exc:
            raise ReportError(f"cannot read dataset cases at {path}: {exc}") from exc
        if sha in datasets_by_sha and datasets_by_sha[sha] != path:
            continue
        datasets_by_sha[sha] = path

    infos: list[dict[str, Any]] = []
    for value in run_dirs:
        path = Path(value)
        try:
            run, _ = load_run(path) if (path / "results.jsonl").is_file() else (
                json.loads((path / "run.json").read_text(encoding="utf-8")),
                _load_jsonl(path / "answers.jsonl"),
            )
            kind, rows = _run_kind(path)
        except (OSError, ValueError, KeyError) as exc:
            raise ReportError(f"cannot load run {path}: {exc}") from exc
        _validate_run(run, rows, path)
        sha = run["dataset_sha256"]
        if sha not in datasets_by_sha:
            raise ReportError(f"{path}: no supplied --dataset has sha256 {sha}")
        dataset_path = datasets_by_sha[sha]
        from .dataset import load_cases
        cases = {case.id: case for case in load_cases(dataset_path / "cases.jsonl")}
        row_ids = {row["case_id"] for row in rows}
        unknown = sorted(row_ids - cases.keys())
        if unknown:
            raise ReportError(f"{path}: run contains case ids absent from dataset: {', '.join(unknown[:10])}")
        included = [cases[cid] for cid in row_ids]
        wrong_split = [case.id for case in included if case.split != run.get("split")]
        if wrong_split:
            raise ReportError(f"{path}: run split {run.get('split')} includes cases from another split: {', '.join(wrong_split[:10])}")
        for row in rows:
            actual_category = cases[row["case_id"]].category
            if row.get("category") is not None and row["category"] != actual_category:
                raise ReportError(f"{path}: category for case {row['case_id']} does not match supplied dataset")
            row["_dataset_category"] = actual_category
        from .review.require import refusal_message, unreviewed_ids
        unreviewed = unreviewed_ids(included)
        if unreviewed:
            raise ReportError(refusal_message(unreviewed, len(included), "report these run cases"))
        if dataset_sha256(dataset_path) != sha:
            raise ReportError(f"{path}: dataset sha256 does not match cases.jsonl")
        rows = _dedupe_rows(rows, path)
        infos.append({"path": path, "run": run, "kind": kind, "rows": rows, "sha": sha,
                      "dataset_path": dataset_path})

    by_sha_profile: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for info in infos:
        run = info["run"]
        by_sha_profile[(info["sha"], run.get("profile_name", "unnamed"))][info["kind"]].append(info)
    # Runs are paired only on exact dataset/profile/split. Any same dataset/profile cross-split pair is suspect.
    for (sha, profile), kinds in by_sha_profile.items():
        retrievals, answers = kinds.get("retrieval", []), kinds.get("answer", [])
        if len(retrievals) > 1 or len(answers) > 1:
            raise ReportError(f"dataset {sha} profile {profile}: ambiguous duplicate run inputs")
        if retrievals and answers:
            r_splits = {i["run"].get("split") for i in retrievals}
            a_splits = {i["run"].get("split") for i in answers}
            if r_splits != a_splits:
                raise ReportError(f"dataset {sha} profile {profile}: retrieval/answer split mismatch")
            if retrievals[0]["run"].get("split") != answers[0]["run"].get("split"):
                raise ReportError(f"dataset {sha} profile {profile}: retrieval/answer split mismatch")
            answer_retrieval_hash = answers[0]["run"].get("retrieval_config_hash")
            if not answer_retrieval_hash:
                raise ReportError(
                    f"dataset {sha} profile {profile}: answer run lacks retrieval_config_hash; "
                    "report it alone for partial results or regenerate it with retrieval provenance"
                )
            if answer_retrieval_hash != retrievals[0]["run"].get("config_hash"):
                raise ReportError(f"dataset {sha} profile {profile}: answer retrieval config hash does not match retrieval run")
            # A retrieval/answer pair must refer to the exact same persisted case set.
            if {r["case_id"] for r in retrievals[0]["rows"]} != {r["case_id"] for r in answers[0]["rows"]}:
                raise ReportError(f"dataset {sha} profile {profile}: retrieval/answer case sets differ")

    rendered = []
    sha_labels = {info["sha"]: info["dataset_path"].name for info in infos}
    for sha in sorted(sha_labels, key=lambda value: (sha_labels[value], value)):
        group_infos = [info for info in infos if info["sha"] == sha]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for (group_sha, profile), kinds in by_sha_profile.items():
            if group_sha != sha:
                continue
            retrieval = kinds.get("retrieval", [None])[0]
            answer = kinds.get("answer", [None])[0]
            for row in _combined_rows(retrieval, answer):
                groups[(row["category"], profile)].append(row)
        label = group_infos[0]["dataset_path"].name
        rendered.append(_markdown_table(label, sha, groups, group_infos))
    return "\n\n---\n\n".join(rendered) + "\n"

"""Offline tests for the measurement report and its refusal boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scribe_eval.report import ReportError, _bootstrap_ci, _p95, build_report


def _write_dataset(root: Path, ids: tuple[str, ...] = ("c1", "c2")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for case_id in ids:
        rows.append({
            "id": case_id, "split": "dev", "category": "policy", "question": f"Q {case_id}",
            "answerable": True, "reference_answer": "answer", "evidence": [], "reviewed": True,
        })
    (root / "cases.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return root


def _write_run(root: Path, dataset: Path, run_id: str, kind: str, rows: list[dict], **overrides) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    run = {
        "run_id": run_id, "dataset_sha256": hashlib.sha256((dataset / "cases.jsonl").read_bytes()).hexdigest(),
        "dataset_name": dataset.name, "split": "dev", "profile_name": "hybrid-test",
        "config_hash": "config-abc", "code_commit": "deadbeef", "model_ids": {"generator": "gemini-2.5-flash"},
        "measurement": True, "stamp": None, "summary": {"review": {"status": "reviewed", "unreviewed_in_split": 0}},
        "leak_rate_verified": True,
    }
    run.update(overrides)
    if kind == "answer":
        from scribe_eval.runs import RunRecord

        record = RunRecord(
            run_id=run_id, dataset_version="synthetic", split=run["split"], profile_name=run["profile_name"],
            config_hash=run["config_hash"], retrieval_config_hash=run.get("retrieval_config_hash"),
            model_ids=run["model_ids"], code_commit=run["code_commit"], measurement=run["measurement"],
            stamp=run["stamp"], dataset_sha256=run["dataset_sha256"], dataset_name=run["dataset_name"],
            summary=run["summary"], leak_rate_verified=run["leak_rate_verified"],
        )
        answer_dir = record.write(root.parent)
        (answer_dir / "answers.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return answer_dir
    (root / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    filename = "results.jsonl"
    (root / filename).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return root


def _retrieval_rows() -> list[dict]:
    return [
        {"case_id": "c1", "category": "policy", "metrics": {"recall@5": 1, "recall@10": 1, "mrr": 1, "ndcg@10": 1},
         "latency_ms": 100, "cost": {"usd": 0.1}, "leak": {"leaked": False, "provenance_missing_items": 0}},
        {"case_id": "c2", "category": "policy", "metrics": {"recall@5": 0, "recall@10": 0.5, "mrr": 0.5, "ndcg@10": 0.5},
         "latency_ms": 200, "cost": {"usd": 0.1}, "leak": {"leaked": False, "provenance_missing_items": 0}},
    ]


def _answer_rows() -> list[dict]:
    return [
        {"case_id": "c1", "category": "policy", "metrics": {"citation_validity": 1, "groundedness": 0.8, "abstention_accuracy": 1},
         "latency_ms": 50, "cost": {"usd": 0.2}, "leak": {"leaked": False, "provenance_missing_items": 0}},
        {"case_id": "c2", "category": "policy", "metrics": {"citation_validity": 0.5, "groundedness": 0.4, "abstention_accuracy": 1},
         "latency_ms": 150, "cost": {"usd": 0.2}, "leak": {"leaked": False, "provenance_missing_items": 0}},
    ]


def test_report_matches_golden_markdown(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path / "dataset-alpha")
    retrieval = _write_run(tmp_path / "retrieval", dataset, "run-retrieval", "retrieval", _retrieval_rows())
    answer = _write_run(tmp_path / "answer", dataset, "run-answer", "answer", _answer_rows(),
                        config_hash="answer-config", retrieval_config_hash="config-abc",
                        model_ids={"generator": "gemini-2.5-flash", "judge": "gemini-2.5-pro"})

    actual = build_report([retrieval, answer], [dataset])
    expected = """## Dataset: dataset-alpha

Dataset sha256: `d5d927d332c7fa3fa65cb9a703eea56f91e9e908553ed215648d4af968fd0ef3`

| Category × profile | Recall@5 | Recall@10 | MRR | nDCG@10 | Citation validity | Groundedness | Abstention accuracy | Leak rate | p95 latency | $/query |
|---|---|---|---|---|---|---|---|---|---|---|
| policy × hybrid-test | 0.500 [0.000, 1.000] (n=2) | 0.750 [0.500, 1.000] (n=2) | 0.750 [0.500, 1.000] (n=2) | 0.750 [0.500, 1.000] (n=2) | 0.750 [0.500, 1.000] (n=2) | 0.600 [0.400, 0.800] (n=2) | 1.000 [1.000, 1.000] (n=2) | 0.000 [0.000, 0.000] (n=2) | 350.000 [150.000, 350.000] (n=2) | 0.300 [0.300, 0.300] (n=2) |
| Overall × hybrid-test | 0.500 [0.000, 1.000] (n=2) | 0.750 [0.500, 1.000] (n=2) | 0.750 [0.500, 1.000] (n=2) | 0.750 [0.500, 1.000] (n=2) | 0.750 [0.500, 1.000] (n=2) | 0.600 [0.400, 0.800] (n=2) | 1.000 [1.000, 1.000] (n=2) | 0.000 [0.000, 0.000] (n=2) | 350.000 [150.000, 350.000] (n=2) | 0.300 [0.300, 0.300] (n=2) |

Values are means with 95% bootstrap CIs, except latency uses the p95 statistic and its bootstrap CI. `n` is contributing cases.

Stage coverage:
- profile `hybrid-test`: retrieval + answer evaluation (generation + verification + semantic judge) (end-to-end evaluation pipeline)

Run provenance:
- run `run-answer`; dataset sha256 `d5d927d332c7fa3fa65cb9a703eea56f91e9e908553ed215648d4af968fd0ef3`; profile `hybrid-test`; answer config hash `answer-config`; retrieval profile config hash `config-abc`; git commit `deadbeef`; models `{"generator": "gemini-2.5-flash", "judge": "gemini-2.5-pro"}`
- run `run-retrieval`; dataset sha256 `d5d927d332c7fa3fa65cb9a703eea56f91e9e908553ed215648d4af968fd0ef3`; profile `hybrid-test`; profile config hash `config-abc`; git commit `deadbeef`; models `{"generator": "gemini-2.5-flash"}`
"""
    # The dataset hash is calculated from exact fixture bytes; substitute it in the pinned output.
    expected = expected.replace("d5d927d332c7fa3fa65cb9a703eea56f91e9e908553ed215648d4af968fd0ef3",
                               hashlib.sha256((dataset / "cases.jsonl").read_bytes()).hexdigest())
    assert actual == expected


def test_report_separates_datasets_and_never_invents_missing_values(tmp_path: Path) -> None:
    first = _write_dataset(tmp_path / "alpha")
    second = _write_dataset(tmp_path / "beta", ("c3",))
    r1 = _write_run(tmp_path / "r1", first, "run-a", "retrieval", _retrieval_rows())
    r2 = _write_run(tmp_path / "r2", second, "run-b", "retrieval", [
        {"case_id": "c3", "category": "policy", "metrics": {"recall@5": 1}, "latency_ms": None,
         "cost": None, "leak": None}
    ])
    report = build_report([r1, r2], [first, second])
    assert report.count("## Dataset:") == 2
    assert "Recall@10 | MRR" in report
    assert "n/a (NOT RECORDED)" in report
    assert report.index("Dataset: alpha") < report.index("Dataset: beta")


def test_refuses_seeded_fake_unreviewed_sha_mismatch_and_ambiguous_pairs(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path / "dataset")
    run = _write_run(tmp_path / "run", dataset, "run-1", "retrieval", _retrieval_rows())
    run_json = json.loads((run / "run.json").read_text())
    for patch, message in [
        ({"stamp": "SEEDED - NOT A MEASUREMENT"}, "seeded"),
        ({"model_ids": {"generator": "fake"}}, "fake model"),
        ({"measurement": False}, "measurement"),
    ]:
        (run / "run.json").write_text(json.dumps({**run_json, **patch}))
        with pytest.raises(ReportError, match=message):
            build_report([run], [dataset])
    wrong_split = {**run_json, "split": "test"}
    (run / "run.json").write_text(json.dumps(wrong_split))
    with pytest.raises(ReportError, match="includes cases from another split"):
        build_report([run], [dataset])
    (run / "run.json").write_text(json.dumps(run_json))

    cases = (dataset / "cases.jsonl").read_text().splitlines()
    changed = json.loads(cases[0])
    changed["reviewed"] = False
    (dataset / "cases.jsonl").write_text(json.dumps(changed) + "\n" + cases[1] + "\n")
    run_json["dataset_sha256"] = hashlib.sha256((dataset / "cases.jsonl").read_bytes()).hexdigest()
    (run / "run.json").write_text(json.dumps(run_json))
    with pytest.raises(ReportError, match="not human-reviewed"):
        build_report([run], [dataset])

    original = _write_dataset(tmp_path / "original")
    bad_run = _write_run(tmp_path / "bad-run", original, "run-2", "retrieval", _retrieval_rows())
    with pytest.raises(ReportError, match="no supplied --dataset"):
        build_report([bad_run], [dataset])

    answer = _write_run(tmp_path / "answer", original, "run-answer", "answer", _answer_rows())
    with pytest.raises(ReportError, match="ambiguous duplicate run inputs"):
        build_report([bad_run, _write_run(tmp_path / "duplicate", original, "run-3", "retrieval", _retrieval_rows()), answer], [original])

    mismatched_answer = _write_run(tmp_path / "mismatched-answer", original, "run-answer-mismatch", "answer",
                                   _answer_rows(), retrieval_config_hash="another-config")
    with pytest.raises(ReportError, match="answer retrieval config hash does not match"):
        build_report([bad_run, mismatched_answer], [original])

    legacy_answer = _write_run(tmp_path / "legacy-answer", original, "run-answer-legacy", "answer", _answer_rows())
    with pytest.raises(ReportError, match="lacks retrieval_config_hash"):
        build_report([bad_run, legacy_answer], [original])
    assert "only (partial; not end-to-end)" in build_report([legacy_answer], [original])


def test_unverified_missing_provenance_does_not_render_zero_leak_rate(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path / "dataset")
    rows = _retrieval_rows()
    rows[0]["leak"]["provenance_missing_items"] = 1
    run = _write_run(tmp_path / "run", dataset, "run-unverified", "retrieval", rows,
                     leak_rate_verified=False)
    report = build_report([run], [dataset])
    assert "Leak rate |" in report
    leak_line = next(line for line in report.splitlines() if "policy × hybrid-test" in line)
    assert "n/a (NOT RECORDED)" in leak_line


def test_p95_confidence_interval_bootstraps_the_p95_statistic() -> None:
    values = [100.0, 200.0]
    assert _p95(values) == 200.0
    assert _bootstrap_ci(values, _p95, seed=7, n_resamples=10_000) == (100.0, 200.0)

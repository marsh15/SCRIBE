"""scripts/arm-gate.sh --check: the preflight refuses (with the exact thing the owner must provide) before any Docker,
database or provider call. These tests never touch a database or a provider."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "arm-gate.sh"
SECRET_A = "AIza-test-secret-value-1"
SECRET_B = "pa-test-secret-value-2"


def _dataset(tmp_path: Path, *, reviewed: bool, count: int = 3) -> Path:
    d = tmp_path / "ds"
    d.mkdir()
    rows = [{"id": f"x-{i}", "split": "validation", "reviewed": reviewed} for i in range(count)]
    rows.append({"id": "t-1", "split": "test", "reviewed": False})  # the test split is never required
    (d / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def _keys(tmp_path: Path, body: str, mode: int = 0o600) -> Path:
    p = tmp_path / "keys.env"
    p.write_text(body)
    p.chmod(mode)
    return p


def _run(tmp_path: Path, keys: Path | None, dataset: Path, *args: str, **extra: str):
    env = {k: v for k, v in os.environ.items() if k not in {"CI", "GOOGLE_GENERATIVE_AI_API_KEY", "VOYAGE_API_KEY"}}
    env.update({"ARM_GATE_KEYS_FILE": str(keys or tmp_path / "absent.env"), "ARM_GATE_DATASET": str(dataset),
                "ARM_GATE_MIN_FREE_MB": "1"}, **extra)
    return subprocess.run([str(SCRIPT), *(args or ("--check",))], cwd=ROOT, env=env, text=True, capture_output=True, check=False)


def both(r) -> str:
    return r.stdout + r.stderr


def test_missing_keys_file_is_reported_as_blocked_with_the_path(tmp_path):
    r = _run(tmp_path, None, _dataset(tmp_path, reviewed=True))
    assert r.returncode == 3
    assert "BLOCKED" in r.stderr and "absent.env" in r.stderr


def test_keys_file_must_be_mode_600(tmp_path):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\nVOYAGE_API_KEY={SECRET_B}\n", mode=0o644)
    r = _run(tmp_path, keys, _dataset(tmp_path, reviewed=True))
    assert r.returncode == 3 and "600" in r.stderr
    assert SECRET_A not in both(r) and SECRET_B not in both(r)


def test_a_missing_variable_is_named_and_values_are_never_printed(tmp_path):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\n")
    r = _run(tmp_path, keys, _dataset(tmp_path, reviewed=True))
    assert r.returncode == 3 and "VOYAGE_API_KEY" in r.stderr
    assert SECRET_A not in both(r)


def test_unreviewed_validation_cases_block_arming_and_the_test_split_does_not(tmp_path):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\nVOYAGE_API_KEY={SECRET_B}\n")
    r = _run(tmp_path, keys, _dataset(tmp_path, reviewed=False, count=3))
    assert r.returncode == 4
    assert "3 of 3" in r.stderr and "--split validation" in r.stderr


def test_preflight_passes_when_keys_and_validation_review_are_in_place(tmp_path):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\nVOYAGE_API_KEY={SECRET_B}\n")
    r = _run(tmp_path, keys, _dataset(tmp_path, reviewed=True))
    assert r.returncode == 0, both(r)
    assert "preflight ok" in r.stdout
    assert SECRET_A not in both(r) and SECRET_B not in both(r)


def test_refuses_to_run_in_ci(tmp_path):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\nVOYAGE_API_KEY={SECRET_B}\n")
    r = _run(tmp_path, keys, _dataset(tmp_path, reviewed=True), CI="true")
    assert r.returncode == 2 and "CI" in r.stderr


def test_low_disk_space_stops_before_anything_heavy(tmp_path):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\nVOYAGE_API_KEY={SECRET_B}\n")
    r = _run(tmp_path, keys, _dataset(tmp_path, reviewed=True), ARM_GATE_MIN_FREE_MB="99999999")
    assert r.returncode == 5 and "disk" in r.stderr.lower()


@pytest.mark.parametrize("arg", ["--help", "-h", "--chek", "--dry-run"])
def test_unknown_arguments_are_refused_instead_of_starting_the_paid_run(tmp_path, arg):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\nVOYAGE_API_KEY={SECRET_B}\n")
    r = _run(tmp_path, keys, _dataset(tmp_path, reviewed=True), arg)
    assert r.returncode == 2 and "usage" in r.stderr


@pytest.mark.parametrize("body", ['VOYAGE_API_KEY=""\n', "VOYAGE_API_KEY=''\n", "VOYAGE_API_KEY=\n"])
def test_empty_or_quoted_empty_values_do_not_count_as_keys(tmp_path, body):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\n{body}")
    r = _run(tmp_path, keys, _dataset(tmp_path, reviewed=True))
    assert r.returncode == 3 and "VOYAGE_API_KEY" in r.stderr
    assert SECRET_A not in both(r)


def test_a_later_duplicate_empty_line_overrides_an_earlier_value(tmp_path):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\nVOYAGE_API_KEY={SECRET_B}\nVOYAGE_API_KEY=\n")
    assert _run(tmp_path, keys, _dataset(tmp_path, reviewed=True)).returncode == 3


def test_an_unreadable_dataset_is_not_reported_as_unreviewed(tmp_path):
    keys = _keys(tmp_path, f"GOOGLE_GENERATIVE_AI_API_KEY={SECRET_A}\nVOYAGE_API_KEY={SECRET_B}\n")
    empty = tmp_path / "nodata"
    empty.mkdir()
    r = _run(tmp_path, keys, empty)
    assert r.returncode == 6 and "could not read the dataset" in r.stderr

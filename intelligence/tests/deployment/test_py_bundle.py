"""The runtime-only Python bundle size guard works offline and honors its threshold override."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check-py-bundle.sh"


def _run(limit: str):
    env = {**os.environ, "PY_BUNDLE_MAX_MB": limit, "INTELLIGENCE_PYTHON": os.sys.executable}
    return subprocess.run([str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False)


def test_current_tree_fits_vercel_runtime_bundle_limit():
    result = _run("500")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "runtime bundle estimate" in result.stdout


def test_bundle_script_fails_at_a_tiny_configured_limit():
    result = _run("0.0001")
    assert result.returncode != 0
    assert "exceeds 0.0 MB" in result.stderr


@pytest.mark.parametrize("limit", ["0", "-1", "nan", "inf", "500.1"])
def test_bundle_script_rejects_invalid_or_over_cap_thresholds(limit):
    result = _run(limit)
    assert result.returncode != 0
    assert "PY_BUNDLE_MAX_MB" in result.stderr

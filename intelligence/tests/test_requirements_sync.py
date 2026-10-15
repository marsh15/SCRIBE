"""The Vercel Python runtime installs the repo-root requirements.txt, while local dev and CI use pyproject.toml.
requirements.txt says CI checks that they match; this is that check."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _normalize(spec: str) -> str:
    return "".join(spec.split()).lower()


def _requirements() -> set[str]:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    return {_normalize(line) for line in lines if line.strip() and not line.strip().startswith("#")}


def _pyproject() -> set[str]:
    data = tomllib.loads((ROOT / "intelligence" / "pyproject.toml").read_text())
    return {_normalize(dep) for dep in data["project"]["dependencies"]}


def test_root_requirements_match_pyproject_runtime_dependencies():
    req, proj = _requirements(), _pyproject()
    assert req == proj, (
        f"requirements.txt only: {sorted(req - proj)}; pyproject.toml only: {sorted(proj - req)}. "
        "Update both files together."
    )

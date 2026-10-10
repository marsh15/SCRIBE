from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "fixtures" / "corpus"
GOLDEN = REPO / "benchmarks" / "datasets" / "golden-v1" / "cases.jsonl"

MIMES = {".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain"}


import pytest  # noqa: E402


@pytest.fixture(scope="session")
def corpus_dir() -> Path:
    return CORPUS


@pytest.fixture(scope="session")
def parse_corpus():
    from scribe_ingest import parse

    cache: dict[str, object] = {}

    def _parse(name: str):
        if name not in cache:
            path = CORPUS / name
            cache[name] = parse(path.read_bytes(), MIMES[path.suffix])
        return cache[name]

    return _parse

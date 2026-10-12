"""SEEDED fixture for the T31 retrieval gate. NOT A MEASUREMENT.

Builds, deterministically and without any network or provider key:
  * a 48-section synthetic corpus (8 markdown docs; words are invented, so they carry no real-world meaning),
  * a 32-case dataset (validation: 14 answerable + 3 unanswerable),
  * a response cache recorded through the gate's own record path with hash-vector embeddings (served by an httpx
    MockTransport, so the cache keys are the production keys) and the token-overlap FakeReranker.

Nothing here is committed except `gate_seeded/baseline.seeded.json` (stamped SEEDED, never armed by default).
The cache is rebuilt in a temp dir by every test that needs it (about 0.8 MB of vectors, so not committed).

Regenerate the committed baseline (needs DATABASE_URL_TEST):
    cd intelligence && DATABASE_URL_TEST=... uv run python tests/evaluation/_gate_seed.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

SEEDED_BASELINE = Path(__file__).parent / "gate_seeded" / "baseline.seeded.json"
SYL = ["zor", "quen", "bli", "mar", "tev", "ox", "pri", "dun", "vek", "sal", "hom", "rud", "fen", "lat", "kis", "wob"]


def word(i: int, salt: int) -> str:
    a, b = SYL[(i * 7 + salt) % 16], SYL[(i * 5 + salt * 3 + 1) % 16]
    return f"{a}{b}{salt}x{i}"


def sections() -> list[dict]:
    out = []
    for d in range(8):
        for s in range(6):
            i = d * 6 + s
            a, b, c = word(i, 1), word(i, 2), word(i, 3)
            sentence = f"The {a} {b} procedure for {c} requires approval within {3 + i % 9} days."
            out.append({"doc": f"seed-doc-{d}.md", "heading": f"{a.capitalize()} rules", "sentence": sentence,
                        "question": f"{a} {b} procedure {c} approval", "i": i})
    return out


def build_corpus(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    by_doc: dict[str, list[dict]] = {}
    for sec in sections():
        by_doc.setdefault(sec["doc"], []).append(sec)
    for name, secs in by_doc.items():
        body = "\n\n".join(f"## {s['heading']}\n\n{s['sentence']}" for s in secs)
        (root / name).write_text(f"# Seeded handbook {name}\n\n{body}\n", encoding="utf-8")
    return root


def build_dataset(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    secs = sections()
    rows = []
    # split by section index: validation = 14 answerable, dev = 10, test = 8
    for sec in secs[:32]:
        i = sec["i"]
        split = "validation" if i < 14 else ("dev" if i < 24 else "test")
        rows.append({"id": f"sd-{i:02d}", "split": split, "category": "single_fact", "question": sec["question"],
                     "answerable": True, "reference_answer": sec["sentence"],
                     "evidence": [{"doc": sec["doc"], "version": 1, "quote": sec["sentence"], "grade": 1}]})
    for k, q in enumerate(["helicopter maintenance budget", "submarine cable depth", "quarterly lunar rainfall"]):
        rows.append({"id": f"su-{k}", "split": "validation", "category": "unanswerable", "question": q,
                     "answerable": False, "reference_answer": "Not stated.", "evidence": []})
    (root / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (root / "meta.json").write_text(json.dumps({"version": "seeded-gate-v0", "review_status": SEEDED}), encoding="utf-8")
    return root


SEEDED = "SEEDED FIXTURE - NOT A MEASUREMENT"


def seeded_providers():
    """Record-mode providers with NO network: hash-vector embeddings via MockTransport + token-overlap reranker.
    The reranker reports the production model name so cache keys equal real ones."""
    from scribe_eval.gate import Providers
    from scribe_ingest.embedding import FakeEmbedder, GeminiEmbedder
    from scribe_intel.retrieval.rerank import FakeReranker

    fake = FakeEmbedder()

    def handler(request: httpx.Request) -> httpx.Response:
        reqs = json.loads(request.content)["requests"]
        return httpx.Response(200, json={"embeddings": [
            {"values": fake._vec(r["content"]["parts"][0]["text"])} for r in reqs]})

    class _Rerank(FakeReranker):
        def __init__(self, model: str) -> None:
            super().__init__()
            self.model = model
            self.is_fake = False  # the seeded fixture is labelled SEEDED by the result stamp, not by model name

    return Providers(
        embedder=lambda cache: GeminiEmbedder(api_key="seeded-test-key-not-real", cache=cache,
                                              transport=httpx.MockTransport(handler)),
        reranker=lambda model: _Rerank(model), mode="record")


def args(**kw):
    base = dict(split="validation", baseline="unused.json", cache="unused.jsonl.gz", dataset="", corpus="",
                database_url=None, profile="hybrid-rrf-rerank", tau=0.5, out=None, refresh_cache=False,
                write_baseline=False, seeded=True, accept_seeded_baseline=False)
    base.update(kw)
    return argparse.Namespace(**base)


def record_cache(work: Path, database_url: str, profile: str = "hybrid-rrf-rerank") -> dict:
    """Record the seeded cache + write a seeded result. Returns paths."""
    from scribe_eval import gate

    corpus, dataset = build_corpus(work / "corpus"), build_dataset(work / "dataset")
    cache_path = work / "cache.jsonl.gz"
    rc = gate.run_gate(args(dataset=str(dataset), corpus=str(corpus), cache=str(cache_path),
                            database_url=database_url, profile=profile, refresh_cache=True),
                       providers=seeded_providers())
    assert rc == 0
    return {"corpus": corpus, "dataset": dataset, "cache": cache_path}


def write_baseline(work: Path, paths: dict, database_url: str, out: Path, profile="hybrid-rrf-rerank") -> Path:
    from scribe_eval import gate

    rc = gate.run_gate(args(dataset=str(paths["dataset"]), corpus=str(paths["corpus"]), cache=str(paths["cache"]),
                            database_url=database_url, profile=profile, baseline=str(out), write_baseline=True))
    assert rc == 0
    return out


if __name__ == "__main__":
    url = os.environ["DATABASE_URL_TEST"]
    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        paths = record_cache(w, url)
        write_baseline(w, paths, url, SEEDED_BASELINE)
    print(f"wrote {SEEDED_BASELINE} ({SEEDED_BASELINE.stat().st_size} bytes)")

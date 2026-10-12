"""Do gemini-embedding-2 and gemini-embedding-2-preview share a vector space? (T12 TODO)

Embeds the same passages with both models at 1536 dims, then reports:
  * cosine of same-text pairs (model A vs model B) -- ~1.0 would mean one space
  * cosine of cross-text pairs (different texts, A vs B) -- the null level
  * top-5 neighbour overlap for dev-split queries (A-vs-A ranking vs B-vs-B ranking)
  * cross-model retrieval: query from A against passages from B (hit rate vs same-model)

Uses the dev split only. The key is read from GOOGLE_GENERATIVE_AI_API_KEY and never printed.
Usage: python -m scribe_eval.embedding_space_check --corpus ../fixtures/corpus --dataset ../benchmarks/datasets/golden-v1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
from pathlib import Path

from scribe_ingest.embedding import GeminiEmbedder

MODELS = ("gemini-embedding-2", "gemini-embedding-2-preview")


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # vectors are L2-normalised by the embedder


def load_passages(corpus: Path, limit: int) -> list[str]:
    out: list[str] = []
    for p in sorted(corpus.iterdir()):
        if p.suffix not in (".md", ".txt") or p.name.endswith(".truth.txt") or p.name == "FACTS.md":
            continue
        for para in re.split(r"\n\s*\n", p.read_text(encoding="utf-8")):
            para = para.strip()
            if len(para) >= 80:
                out.append(para[:1200])
    step = max(1, len(out) // limit)
    return out[::step][:limit]


def load_queries(dataset: Path, n: int) -> list[str]:
    qs = []
    for line in (dataset / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        if c.get("split") == "dev" and c.get("answerable"):
            qs.append(c["question"])
    return qs[:n]


def topk(q: list[float], docs: list[list[float]], k: int) -> list[int]:
    return sorted(range(len(docs)), key=lambda i: -_dot(q, docs[i]))[:k]


async def run(corpus: Path, dataset: Path, n_pass: int, n_q: int) -> dict:
    passages = load_passages(corpus, n_pass)
    queries = load_queries(dataset, n_q)
    emb = {m: GeminiEmbedder(model=m, dimensions=1536) for m in MODELS}
    pv, qv = {}, {}
    for m in MODELS:
        pv[m] = await emb[m].embed_documents(passages)
        qv[m] = await emb[m].embed_queries(queries)
    a, b = MODELS
    same = [_dot(x, y) for x, y in zip(pv[a], pv[b])]
    cross = [_dot(pv[a][i], pv[b][j]) for i in range(len(passages)) for j in range(len(passages)) if i != j]
    qsame = [_dot(x, y) for x, y in zip(qv[a], qv[b])]
    overlap = []
    cross_agree = []
    for qa, qb in zip(qv[a], qv[b]):
        ta = topk(qa, pv[a], 5)
        tb = topk(qb, pv[b], 5)
        overlap.append(len(set(ta) & set(tb)) / 5)
        tx = topk(qa, pv[b], 5)  # A query vs B passages
        cross_agree.append(len(set(ta) & set(tx)) / 5)
    return {
        "models": list(MODELS),
        "dims": 1536,
        "n_passages": len(passages),
        "n_queries": len(queries),
        "same_text_cosine": {"mean": statistics.mean(same), "min": min(same), "max": max(same)},
        "query_same_text_cosine": {"mean": statistics.mean(qsame), "min": min(qsame), "max": max(qsame)},
        "cross_text_cosine_AvsB": {"mean": statistics.mean(cross), "min": min(cross), "max": max(cross)},
        "top5_overlap_AA_vs_BB": statistics.mean(overlap),
        "top5_overlap_AA_vs_A-query-on-B-passages": statistics.mean(cross_agree),
        "requests": {m: emb[m].requests_made for m in MODELS},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--passages", type=int, default=50)
    ap.add_argument("--queries", type=int, default=20)
    args = ap.parse_args()
    res = asyncio.run(run(args.corpus, args.dataset, args.passages, args.queries))
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

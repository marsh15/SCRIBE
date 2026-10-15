"""`scribe-eval run-profile`: run a retrieval profile (A-E) against the ALREADY INGESTED eval workspace and write
a rankings JSONL for `scribe-eval score` (M1 / T17 groundwork).

* Never reads the environment for a database URL, never scores or runs the test split (INV-12).
* Rerank profiles (dense-rerank, hybrid-rrf-rerank) need VOYAGE_API_KEY; without it they are REFUSED here instead of
  silently degrading to the fused order (which would mislabel a no-rerank result as a rerank result).
* Query embeddings use the same disk cache as `ingest-corpus`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import psycopg
from psycopg_pool import AsyncConnectionPool

from .dataset import load_cases
from .ingest_corpus import EVAL_OWNER

RANKING_DEPTH = 20
SPLITS = ("dev", "validation")


class RunProfileError(Exception):
    pass


def check_profile(name: str, env: "os._Environ[str] | dict[str, str]") -> None:
    from scribe_intel.retrieval.profiles import BY_NAME

    prof = BY_NAME.get(name)
    if prof is None:
        raise RunProfileError(f"unknown profile {name!r}; choose one of {sorted(BY_NAME)}")
    if prof.config.rerank and not env.get("VOYAGE_API_KEY"):
        raise RunProfileError(f"profile {name!r} reranks with Voyage and VOYAGE_API_KEY is not set; refusing to "
                              "run it degraded")


def run(args) -> int:
    if args.split not in SPLITS:
        print("ERROR: only dev and validation can be run (the test split is milestone-only, INV-12).", file=sys.stderr)
        return 2
    if not args.database_url:
        print("ERROR: --database-url is required (the environment is never consulted).", file=sys.stderr)
        return 2
    if "test" not in args.database_url.rsplit("/", 1)[-1].split("?")[0]:
        print("ERROR: refusing a database whose name does not contain 'test'.", file=sys.stderr)
        return 2
    try:
        check_profile(args.profile, os.environ)
        return asyncio.run(_go(args))
    except RunProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


async def _go(args) -> int:
    from scribe_ingest.embedding import GeminiEmbedder
    from scribe_intel.retrieval.profiles import BY_NAME
    from scribe_intel.retrieval.registry import RetrieveContext, get_handler
    from scribe_intel.retrieval.rerank import CachedReranker, VoyageReranker

    from .cache import DiskCache
    from .embed_cache import DiskEmbeddingCache

    cases = [c for c in load_cases(Path(args.dataset) / "cases.jsonl") if c.split == args.split]
    if args.limit:
        cases = cases[: args.limit]
    cache = DiskCache(args.cache_path)
    embedder = GeminiEmbedder(cache=DiskEmbeddingCache(cache))
    handler = get_handler(args.profile)
    profile = BY_NAME[args.profile]
    voyage_key = os.environ.get("VOYAGE_API_KEY", "")

    def reranker_for(model: str):
        return CachedReranker(VoyageReranker(api_key=voyage_key, model=model), cache) if voyage_key else None

    async with await psycopg.AsyncConnection.connect(args.database_url, autocommit=True, prepare_threshold=None) as conn:
        row = await (await conn.execute("select id, kind from workspaces where owner_user_id = %s", (EVAL_OWNER,))).fetchone()
        if not row or row[1] != "eval":
            raise RunProfileError("no eval workspace found; run `scribe-eval ingest-corpus` first")
        wid = str(row[0])
    pool = AsyncConnectionPool(args.database_url, min_size=0, max_size=3, open=False,
                               kwargs={"prepare_threshold": None, "autocommit": True})
    await pool.open()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lat: list[float] = []
    degraded = 0
    try:
        with out_path.open("w", encoding="utf-8") as fh:
            for case in cases:
                ctx = RetrieveContext(pool=pool, workspace_id=wid, query=case.question, source_ids=None,
                                      limit=RANKING_DEPTH, embedder_for=lambda dims: embedder,
                                      reranker_for=reranker_for)
                t0 = time.monotonic()
                res = await handler(ctx)
                ms = (time.monotonic() - t0) * 1000
                lat.append(ms)
                degraded += 1 if res.degraded else 0

                def score(r):
                    # native score of the final ordering: rerank, else RRF only when two lists were fused, else the
                    # single retriever's own score (cosine / ts_rank). Informational; scales differ across profiles.
                    if r.rerank_score is not None:
                        return r.rerank_score
                    if r.dense_rank is not None and r.lexical_rank is not None:
                        return r.fusion_score
                    return r.dense_score if r.dense_rank is not None else r.lexical_score

                fh.write(json.dumps({
                    "case_id": case.id,
                    "ranking": [{"chunk_id": str(r.chunk_id), "doc": r.candidate.doc_name, "text": r.candidate.content,
                                 "score": score(r), "method": "hybrid"} for r in res.ranked[:RANKING_DEPTH]],
                    "latency_ms": round(ms, 1),
                    "cost": {"embedding_cache_stats": dict(cache.stats)},
                    "degraded": list(res.degraded),
                    "meta": {"profile": args.profile, "config_hash": profile.config_hash, "stage_ms": res.stage_ms},
                }) + "\n")
    finally:
        await pool.close()
    lat.sort()
    p95 = lat[max(0, int(0.95 * len(lat)) - 1)] if lat else 0.0
    print(f"profile {args.profile} split {args.split}: {len(cases)} cases, degraded {degraded}, latency median "
          f"{lat[len(lat) // 2]:.0f} ms p95 {p95:.0f} ms, embedding requests {embedder.requests_made}, wrote {out_path}")
    return 0

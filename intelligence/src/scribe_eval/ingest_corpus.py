"""`scribe-eval ingest-corpus`: index the eval corpus into an 'eval' workspace with the same
indexer the service uses (T12). Never falls back to environment database URLs."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Callable

import psycopg

from scribe_ingest.embedding import Embedder, FakeEmbedder, GeminiEmbedder
from scribe_ingest.indexer import IngestResult, build_generation, load_index_profile, register_version

EVAL_OWNER = "eval-golden-v1"
MIMES = {
    ".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain", ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
SKIP = {"FACTS.md"}
EXPECTED_FAILURES = {"facility-safety-notice-scanned.pdf": "OCR_NOT_AVAILABLE"}  # until T21
DEFAULT_CACHE = "benchmarks/cache/py-embeddings.sqlite"
Loader = Callable[[str], bytes]


class CliError(Exception):
    pass


def list_corpus(corpus: Path) -> list[str]:
    return sorted(p.name for p in corpus.iterdir()
                  if p.suffix.lower() in MIMES and p.name not in SKIP and not p.name.endswith(".truth.txt"))


async def ensure_eval_workspace(conn: psycopg.AsyncConnection) -> str:
    cur = await conn.execute("select id, kind from workspaces where owner_user_id = %s", (EVAL_OWNER,))
    rows = await cur.fetchall()
    for _, kind in rows:
        if kind != "eval":
            raise CliError(f"refusing: workspace owned by {EVAL_OWNER!r} has kind {kind!r}, not 'eval'")
    if rows:
        return str(rows[0][0])
    cur = await conn.execute("insert into workspaces(kind, owner_user_id) values ('eval', %s) returning id",
                             (EVAL_OWNER,))
    return str((await cur.fetchone())[0])


async def reset_workspace(conn: psycopg.AsyncConnection, wid: str) -> None:
    """Wipe ONLY this (eval) workspace's index and file rows."""
    async with conn.transaction():
        await conn.execute("select set_config('app.workspace_id', %s, true)", (wid,))
        for table in ("active_indexes", "chunk_embeddings_1536", "chunk_embeddings_3072", "chunk_source_spans",
                      "chunks", "document_blocks", "index_generations"):
            await conn.execute(f"delete from {table} where workspace_id = %s", (wid,))  # noqa: S608 - fixed names
        await conn.execute("update files set desired_version_id = null, active_version_id = null where workspace_id = %s", (wid,))
        await conn.execute("delete from source_versions where workspace_id = %s", (wid,))
        await conn.execute("delete from files where workspace_id = %s", (wid,))


async def ingest_corpus(
    conn: psycopg.AsyncConnection, wid: str, names: list[str], loader: Loader, embedder: Embedder,
    *, reset: bool = False,
) -> list[tuple[str, IngestResult]]:
    kind = (await (await conn.execute("select kind from workspaces where id = %s", (wid,))).fetchone() or [None])[0]
    if kind != "eval":
        raise CliError(f"refusing: workspace {wid} is kind {kind!r}, not 'eval'")
    if reset:
        await reset_workspace(conn, wid)
    profile = await load_index_profile(conn)
    out: list[tuple[str, IngestResult]] = []
    for name in names:
        data = loader(name)
        mime = MIMES[Path(name).suffix.lower()]
        cur = await conn.execute("select id from files where workspace_id = %s and name = %s", (wid, name))
        row = await cur.fetchone()
        if row:
            fid = row[0]
        else:
            cur = await conn.execute(
                "insert into files(name, type, size, user_id, workspace_id, storage_key, status) "
                "values (%s,%s,%s,%s,%s,%s,'processing') returning id",
                (name, mime, len(data), EVAL_OWNER, wid, f"eval/{name}"))
            fid = (await cur.fetchone())[0]
        ver = await register_version(conn, wid, fid, data, mime, created_by=EVAL_OWNER, storage_key=f"eval/{name}")
        out.append((name, await build_generation(conn, wid, fid, ver, data, mime, embedder, name=name, profile=profile)))
    return out


def evaluate(results: list[tuple[str, IngestResult]]) -> list[str]:
    problems = []
    for name, r in results:
        want = EXPECTED_FAILURES.get(name)
        if want:
            if r.status == "FAILED" and r.error_code != want:
                problems.append(f"{name}: failed with {r.error_code}, expected {want}")
            continue
        if r.status != "ACTIVE" or r.embedded != r.expected or r.expected == 0:
            problems.append(f"{name}: status={r.status} embedded={r.embedded}/{r.expected} error={r.error_code}")
    return problems


def format_table(results: list[tuple[str, IngestResult]]) -> str:
    head = f"{'document':<40}{'blocks':>7}{'parents':>8}{'children':>9}{'emb/exp':>10}  {'status':<10}{'warnings':>9}  error"
    lines = [head, "-" * len(head)]
    for name, r in results:
        err = f"{r.error_code}: {r.message}" if r.error_code else ""
        lines.append(f"{name:<40}{r.blocks:>7}{r.parents:>8}{r.children:>9}{f'{r.embedded}/{r.expected}':>10}  "
                     f"{r.status:<10}{len(r.warnings):>9}  {err}")
    return "\n".join(lines)


def run(args) -> int:
    if not args.database_url:
        print("ERROR: --database-url is required (the environment is never consulted, to avoid "
              "indexing into production).", file=sys.stderr)
        return 2
    corpus = Path(args.corpus)
    if not corpus.is_dir():
        print(f"ERROR: corpus directory not found: {corpus}", file=sys.stderr)
        return 2
    cache = None
    if args.embedder == "fake":
        embedder: Embedder = FakeEmbedder()
        print("WARNING: FAKE embeddings (hash vectors, no semantic meaning). Use --reset before a real "
              "(gemini) run, otherwise ACTIVE fake generations are kept as they are.", file=sys.stderr)
    else:
        from .cache import DiskCache
        from .embed_cache import DiskEmbeddingCache

        cache = DiskCache(args.cache_path)
        embedder = GeminiEmbedder(cache=DiskEmbeddingCache(cache))

    async def go() -> int:
        async with await psycopg.AsyncConnection.connect(args.database_url, autocommit=True,
                                                         prepare_threshold=None) as conn:
            wid = await ensure_eval_workspace(conn)
            results = await ingest_corpus(conn, wid, list_corpus(corpus), lambda n: (corpus / n).read_bytes(),
                                          embedder, reset=args.reset)
        print(f"workspace {wid} (kind eval), profile {args.profile}, embedder "
              f"{'FAKE' if embedder.is_fake else embedder.model}")  # type: ignore[attr-defined]
        print(format_table(results))
        total = sum(r.expected for _, r in results)
        print(f"\nchunks to embed: {total}; embedding requests this run: {getattr(embedder, 'requests_made', 0)}"
              + (f"; cache {cache.stats}" if cache else ""))
        problems = evaluate(results)
        for p in problems:
            print(f"PROBLEM: {p}", file=sys.stderr)
        return 1 if problems else 0

    try:
        return asyncio.run(go())
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

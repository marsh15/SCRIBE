#!/usr/bin/env python3
"""Load the Northwind demo corpus (fixtures/corpus) into ONE account's personal workspace.

Run from the repo root (the environment of the Python service provides the imports):

    uv run --project intelligence python scripts/demo-seed/demo_seed.py --user-id user_xxx --database-url URL            # dry run
    uv run --project intelligence python scripts/demo-seed/demo_seed.py --user-id user_xxx --database-url URL --apply    # writes

Design (docs/scribe2/demo-script.md):
  * It uses the same indexer as the service (register_version + build_generation), NOT the browser upload path, because
    that path needs Vercel Blob and a signed-in session. Consequence: the seeded Sources have no stored original, so the
    "Original" tab and PDF highlighting of the file view are unavailable for them. Chat, citations, the evidence
    inspector, claim verification and the Structure/Chunks/Versions tabs work from the database.
  * DRY RUN is the default. Nothing connects to a database unless --apply is given.
  * Idempotent: a second run finds the same bytes (sha256) and an ACTIVE generation and does nothing.
  * Safety guard: refuses a database that looks like production unless --i-know is passed (see assess_target).
  * Refuses an account whose workspace already holds other files unless --allow-existing (the point is a NEW account).
  * The database URL is never read from the environment, and the password is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = REPO / "fixtures" / "corpus"
DEFAULT_CACHE = REPO / "benchmarks" / "cache" / "py-embeddings.sqlite"

MIMES = {
    ".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain", ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
SKIP = {"FACTS.md"}  # labels for evaluation, not demo content

PRODUCTION_HINT = re.compile(r"prod|live|main|master|primary|neondb|release|customer", re.I)
NON_PRODUCTION_HINT = re.compile(r"demo|dev|test|staging|preview|sandbox|local|scratch", re.I)
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
USER_ID = re.compile(r"^[A-Za-z0-9_\-]{3,128}$")


class Refused(Exception):
    """A safety rule stopped the run. The message says which rule and how to proceed."""


@dataclass(frozen=True)
class Target:
    host: str
    dbname: str

    def __str__(self) -> str:  # never includes user or password
        return f"{self.host or '(default socket)'}/{self.dbname}"


def parse_target(url: str) -> Target:
    parts = urlsplit(url)
    if parts.scheme not in ("postgresql", "postgres"):
        raise Refused("--database-url must be a postgresql:// URL")
    return Target(host=(parts.hostname or "").lower(), dbname=unquote(parts.path.lstrip("/")))


def assess_target(url: str) -> tuple[bool, str]:
    """(looks_safe, reason). Conservative on purpose: when in doubt it is 'not safe'.

    Looks like production when the database name or host matches prod/live/main/master/primary/neondb/release/customer,
    when the name is empty, or when the host is not local and the name carries no demo/dev/test/staging/preview/sandbox hint.
    """
    t = parse_target(url)
    if not t.dbname:
        return False, "the URL names no database"
    hit = PRODUCTION_HINT.search(t.dbname) or PRODUCTION_HINT.search(t.host)
    if hit:
        return False, f"{t} matches the production hint {hit.group(0)!r}"
    if t.host not in LOCAL_HOSTS and not NON_PRODUCTION_HINT.search(t.dbname):
        return False, f"{t} is a remote database whose name has no demo/dev/test/staging/preview/sandbox hint"
    return True, f"{t} looks like a local or non-production database"


def guard(url: str, i_know: bool) -> str:
    safe, reason = assess_target(url)
    if safe:
        return reason
    if i_know:
        return f"WARNING: {reason}; continuing because --i-know was given"
    raise Refused(f"refusing: {reason}. If this really is the database to seed, repeat with --i-know.")


def validate_user_id(user_id: str) -> str:
    if not USER_ID.match(user_id or ""):
        raise Refused("--user-id must be the Clerk user id of the demo account (letters, digits, '_' or '-')")
    if user_id.startswith("eval-"):
        raise Refused("--user-id looks like an eval owner; the demo seed only writes personal workspaces")
    return user_id


def corpus_files(corpus: Path, only: list[str] | None = None) -> list[Path]:
    names = sorted(p for p in corpus.iterdir()
                   if p.suffix.lower() in MIMES and p.name not in SKIP and not p.name.endswith(".truth.txt"))
    if only:
        wanted = set(only)
        missing = wanted - {p.name for p in names}
        if missing:
            raise Refused(f"--only names not in the corpus: {sorted(missing)}")
        names = [p for p in names if p.name in wanted]
    return names


def plan_lines(files: list[Path]) -> list[str]:
    out = []
    for p in files:
        data = p.read_bytes()
        out.append(f"  {p.name:<42}{MIMES[p.suffix.lower()].split('/')[-1][:12]:<14}{len(data):>9} B  sha256 {hashlib.sha256(data).hexdigest()[:12]}")
    return out


async def seed(conn, user_id: str, files: list[Path], embedder, ocr, *, allow_existing: bool = False):
    """Ensure the personal workspace and index every file. Returns (workspace_id, [(name, IngestResult)])."""
    from scribe_ingest.indexer import build_generation, load_index_profile, register_version

    cur = await conn.execute(
        "select id from workspaces where kind = 'personal' and owner_user_id = %s", (user_id,))
    row = await cur.fetchone()
    if row is None:
        await conn.execute("insert into workspaces(kind, owner_user_id) values ('personal', %s) on conflict do nothing",
                           (user_id,))
        cur = await conn.execute(
            "select id from workspaces where kind = 'personal' and owner_user_id = %s", (user_id,))
        row = await cur.fetchone()
    wid = str(row[0])
    await conn.execute("insert into workspace_members(workspace_id, user_id, role) values (%s,%s,'owner') "
                       "on conflict do nothing", (wid, user_id))

    names = {p.name for p in files}
    cur = await conn.execute("select name from files where workspace_id = %s and deleted_at is null", (wid,))
    foreign = sorted({r[0] for r in await cur.fetchall()} - names)
    if foreign and not allow_existing:
        raise Refused(f"the workspace of {user_id} already holds {len(foreign)} other file(s) (e.g. {foreign[0]!r}); "
                      "the demo seed is meant for a NEW account. Use --allow-existing to add the demo files anyway.")

    profile = await load_index_profile(conn)
    results = []
    for p in files:
        data = p.read_bytes()
        mime = MIMES[p.suffix.lower()]
        cur = await conn.execute("select id from files where workspace_id = %s and name = %s and deleted_at is null",
                                 (wid, p.name))
        existing = await cur.fetchone()
        if existing:
            fid = existing[0]
        else:
            cur = await conn.execute(
                "insert into files(name, type, size, user_id, workspace_id, storage_key, status) "
                "values (%s,%s,%s,%s,%s,%s,'processing') returning id",
                (p.name, mime, len(data), user_id, wid, f"demo/{p.name}"))
            fid = (await cur.fetchone())[0]
        ver = await register_version(conn, wid, fid, data, mime, created_by=user_id, storage_key=f"demo/{p.name}")
        results.append((p.name, await build_generation(conn, wid, fid, ver, data, mime, embedder, name=p.name,
                                                      profile=profile, ocr=ocr)))
    return wid, results


def build_providers(kind: str, database_url: str, cache_path: Path):
    from scribe_ingest.embedding import GeminiEmbedder, require_fake_allowed
    from scribe_ingest.ocr import FakeOcrClient, GeminiOcrClient

    if kind == "fake":
        env = dict(os.environ)
        env["SCRIBE_ALLOW_FAKE_EMBEDDINGS"] = "1"  # still refused unless the database name contains 'test'
        try:
            embedder = require_fake_allowed(database_url, env)
        except RuntimeError as exc:  # FakeEmbedderRefused
            raise Refused(str(exc)) from exc
        print("WARNING: FAKE embeddings (hash vectors). Search quality is meaningless; plumbing checks only.",
              file=sys.stderr)
        return embedder, FakeOcrClient(default="[FAKE OCR - NOT REAL TEXT] placeholder transcription.")
    if not os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY"):
        raise Refused("GOOGLE_GENERATIVE_AI_API_KEY is not set (needed for embeddings and OCR of the scanned PDF). "
                      "Export it in your shell, or run without --apply for the dry run.")
    from scribe_eval.cache import DiskCache
    from scribe_eval.embed_cache import DiskEmbeddingCache

    return GeminiEmbedder(cache=DiskEmbeddingCache(DiskCache(str(cache_path)))), GeminiOcrClient()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", required=True, help="required; the environment is never consulted")
    ap.add_argument("--user-id", required=True, help="Clerk user id of the NEW demo account (sign in once first)")
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    ap.add_argument("--only", action="append", help="seed only this corpus file (repeatable)")
    ap.add_argument("--apply", action="store_true", help="actually write; without it this is a dry run")
    ap.add_argument("--embedder", choices=["gemini", "fake"], default="gemini")
    ap.add_argument("--cache-path", default=str(DEFAULT_CACHE))
    ap.add_argument("--allow-existing", action="store_true", help="allow a workspace that already holds other files")
    ap.add_argument("--i-know", action="store_true", help="allow a database that looks like production")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        verdict = guard(args.database_url, args.i_know)
        user_id = validate_user_id(args.user_id)
        corpus = Path(args.corpus)
        if not corpus.is_dir():
            raise Refused(f"corpus directory not found: {corpus}")
        files = corpus_files(corpus, args.only)
        print(verdict)
        print(f"account {user_id}; {len(files)} document(s); embedder {args.embedder}")
        if not args.apply:
            print("DRY RUN (no database connection made). Would index:")
            print("\n".join(plan_lines(files)))
            print("Repeat with --apply to write. Needs GOOGLE_GENERATIVE_AI_API_KEY unless --embedder fake on a *test* DB.")
            return 0
        embedder, ocr = build_providers(args.embedder, args.database_url, Path(args.cache_path))

        import psycopg

        async def go() -> int:
            async with await psycopg.AsyncConnection.connect(args.database_url, autocommit=True,
                                                             prepare_threshold=None) as conn:
                wid, results = await seed(conn, user_id, files, embedder, ocr, allow_existing=args.allow_existing)
            bad = 0
            for name, r in results:
                ok = r.status == "ACTIVE" and r.embedded == r.expected and r.expected > 0
                bad += 0 if ok else 1
                print(f"  {name:<42}{r.status:<10}{r.embedded}/{r.expected}" + ("" if ok else f"  {r.error_code}: {r.message}"))
            print(f"workspace {wid}: {len(results) - bad} ready, {bad} problem(s)")
            return 1 if bad else 0

        return asyncio.run(go())
    except Refused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

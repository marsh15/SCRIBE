"""Run the Python service's cross-workspace tests with the app connected as a REAL non-owner login.

Creates a throwaway database and a login role that is a member of scribe_app (NOSUPERUSER, NOBYPASSRLS), applies the
schema and migrations with the admin URL, then runs the service's endpoint suites (IDOR, adversarial documents, log canary, evidence, ingest, verify, retrieve, compare) with APP_DATABASE_URL_TEST set
to that login. The fixtures still seed and inspect with the admin URL (DATABASE_URL_TEST); only the service under test
loses its superuser. Everything is dropped afterwards. Local PostgreSQL test databases only.

  uv run --project intelligence python scripts/test-python-rls.py postgresql://scribe:scribe@localhost:54329/<db containing test>
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

SUITES = [
    "tests/security/test_idor_python.py", "tests/security/test_adversarial_documents.py", "tests/security/test_logs_canary.py",
    "tests/integration/test_evidence_open.py", "tests/integration/test_ingest_endpoint.py", "tests/integration/test_verify.py",
    "tests/retrieval/test_retrieve_endpoint.py", "tests/retrieval/test_compare.py",
]


def main() -> int:
    url = sys.argv[1] if len(sys.argv) >= 2 else os.environ.get("DATABASE_URL_TEST", "")
    suites = sys.argv[2:] or SUITES  # optional: pytest targets/options to run instead of the default suites
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or "test" not in parsed.path.lower():
        raise SystemExit("refusing: provide a local PostgreSQL test URL (database name must contain 'test')")
    if set(parse_qs(parsed.query)) & {"host", "hostaddr", "port", "dbname", "user", "password", "service", "servicefile"}:
        raise SystemExit("refusing: connection target overrides in URL query")
    root = Path(__file__).resolve().parents[1]
    tag = uuid.uuid4().hex[:10]
    database, role = f"scribe_py_{tag}_test", f"scribe_py_login_{tag}"
    password = secrets.token_urlsafe(24)
    admin_target = urlunsplit(parsed._replace(path=f"/{database}"))
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    authority = f"{role}:{quote(password)}@{host}" + (f":{parsed.port}" if parsed.port else "")
    app_target = urlunsplit(parsed._replace(netloc=authority, path=f"/{database}"))
    with psycopg.connect(url, autocommit=True) as admin:
        if not admin.execute("select rolsuper from pg_roles where rolname=current_user").fetchone()[0]:
            raise SystemExit("refusing: local test setup requires a superuser")
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(database)))
        try:
            subprocess.run([str(root / "scripts/apply-migrations.sh"), admin_target], cwd=root, check=True, stdout=subprocess.DEVNULL)
            admin.execute(sql.SQL("create role {} login nosuperuser nocreatedb nocreaterole nobypassrls password {}").format(sql.Identifier(role), sql.Literal(password)))
            try:
                admin.execute(sql.SQL("grant scribe_app to {}").format(sql.Identifier(role)))
                env = {**os.environ, "DATABASE_URL_TEST": admin_target, "APP_DATABASE_URL_TEST": app_target}
                return subprocess.run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", *suites], cwd=root / "intelligence", env=env).returncode
            finally:
                admin.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(database)))
                admin.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(role)))
        finally:
            admin.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(database)))


if __name__ == "__main__":
    raise SystemExit(main())

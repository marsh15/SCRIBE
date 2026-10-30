"""Run exported chat/search functions using a temporary local database and a real non-owner login."""
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


def main() -> int:
    url = sys.argv[1] if len(sys.argv) == 2 else os.environ.get("DATABASE_URL_TEST", "")
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or "test" not in parsed.path.lower():
        raise SystemExit("refusing: provide a local PostgreSQL test URL")
    if set(parse_qs(parsed.query)) & {"host", "hostaddr", "port", "dbname", "user", "password", "service", "servicefile"}:
        raise SystemExit("refusing: connection target overrides in URL query")
    root = Path(__file__).resolve().parents[1]
    tag = uuid.uuid4().hex[:10]
    database, role = f"scribe_chat_{tag}_test", f"scribe_chat_login_{tag}"
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
                env = {**os.environ, "CHAT_RLS_DATABASE_URL": app_target, "CHAT_RLS_ADMIN_URL": admin_target}
                return subprocess.run(["npx", "vitest", "run", "__tests__/chat-rls-login.db.test.ts"], cwd=root, env=env).returncode
            finally:
                admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(database)))
                admin.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))
        finally:
            admin.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(database)))


if __name__ == "__main__":
    raise SystemExit(main())

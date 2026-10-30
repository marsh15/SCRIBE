"""The workspace bootstrap function must work without an RLS context as scribe_app."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from _db import purge_workspaces
from conftest import DATABASE_URL_TEST, needs_db

pytestmark = needs_db


def resolve(user: str):
    with psycopg.connect(DATABASE_URL_TEST) as conn:
        conn.execute("set local session authorization scribe_app")
        return conn.execute("select public.app_ensure_personal_workspace(%s)", (user,)).fetchone()[0]


def test_resolver_as_runtime_role_without_context():
    users = [f"resolver-{uuid4()}", f"resolver-{uuid4()}"]
    ids = set()
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(resolve, [users[0]] * 6))
        ids.update(results)
        assert len(ids) == 1
        other = resolve(users[1])
        ids.add(other)
        assert len(ids) == 2
        assert resolve(users[0]) == results[0]
        with psycopg.connect(DATABASE_URL_TEST) as conn:
            for user, wid in zip(users, [results[0], other]):
                assert conn.execute(
                    "select user_id, role from workspace_members where workspace_id = %s", (wid,)
                ).fetchall() == [(user, "owner")]
        with psycopg.connect(DATABASE_URL_TEST) as conn:
            conn.execute("set local session authorization scribe_app")
            with pytest.raises(psycopg.Error, match="user id required"):
                conn.execute("select public.app_ensure_personal_workspace(%s)", ("",))
    finally:
        with psycopg.connect(DATABASE_URL_TEST) as conn:
            ids.update(row[0] for row in conn.execute(
                "select id from workspaces where owner_user_id = any(%s)", (users,)
            ).fetchall())
        purge_workspaces(DATABASE_URL_TEST, ids)

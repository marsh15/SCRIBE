"""P1-1: the shared guard FAILS under CI and SKIPS locally (naming the purge helper) when foreign claimable jobs exist."""
from __future__ import annotations

import pytest
from _db import aconnect, fail_or_skip_foreign_jobs, new_file, new_workspace, skip_or_fail_if_foreign_jobs
from conftest import DATABASE_URL_TEST, needs_db

pytestmark = needs_db


def test_policy_fails_in_ci_and_skips_locally(monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="purge_workspaces"):
        fail_or_skip_foreign_jobs()
    monkeypatch.delenv("CI")
    with pytest.raises(pytest.skip.Exception, match="purge_workspaces"):
        fail_or_skip_foreign_jobs()


async def test_a_claimable_foreign_job_trips_the_guard(monkeypatch):
    conn = await aconnect(DATABASE_URL_TEST)
    try:
        cur = await conn.execute("select count(*) from ingestion_jobs where status in ('queued','retrying','processing')")
        if (await cur.fetchone())[0]:
            pytest.skip("foreign claimable jobs present (this test needs a clean queue)")
        wid = await new_workspace(conn)
        fid = await new_file(conn, wid)
        await conn.execute("insert into ingestion_jobs(file_id, status) values (%s,'queued')", (fid,))
        try:
            monkeypatch.setenv("CI", "1")
            watcher = await aconnect(DATABASE_URL_TEST)
            with pytest.raises(pytest.fail.Exception):
                await skip_or_fail_if_foreign_jobs(watcher)
            monkeypatch.delenv("CI")
            watcher = await aconnect(DATABASE_URL_TEST)
            with pytest.raises(pytest.skip.Exception):
                await skip_or_fail_if_foreign_jobs(watcher)
        finally:
            await conn.execute("delete from ingestion_jobs where file_id=%s", (fid,))
    finally:
        await conn.close()

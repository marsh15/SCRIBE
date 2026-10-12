"""T30: publish-run is idempotent, and the dashboard queries (lib/eval/queries.ts) run against a real database over
SEEDED runs. Every row this test writes is removed again."""
from __future__ import annotations

import json
import re
import uuid

import psycopg
import pytest
from _db import REPO, aconnect, new_workspace, purge_workspaces
from conftest import DATABASE_URL_TEST, needs_db
from scribe_eval.publish import publish_run

pytestmark = needs_db
SRC = REPO / "lib" / "eval" / "queries.ts"
DATASET = REPO / "benchmarks" / "datasets" / "golden-v1"


def ts_sql(function: str) -> str:
    ts = SRC.read_text()
    body = ts[ts.index(f"export async function {function}"):]
    body = body[body.index("sql`") + 4:]
    body = body[:body.index("`)")]
    named = re.sub(r"\$\{(\w+)\}", r"@@\1@@", body)
    return re.sub(r"@@(\w+)@@", r"%(\1)s", named.replace("%", "%%"))


def write_run(tmp_path, run_id, rows, *, kind="retrieval", measurement=True):
    d = tmp_path / run_id
    d.mkdir()
    summary = {"overall": {"n": 1, "mean": {"ndcg@10": 0.5}, "ci95": {"ndcg@10": [0.4, 0.6]}}, "per_category": {}}
    (d / "run.json").write_text(json.dumps({
        "run_id": run_id, "dataset_version": "golden-v1", "split": "dev", "profile_name": "seeded-profile",
        "config_hash": "h" * 64, "code_commit": "abc1234", "model_ids": {"embed": "fake"}, "prompt_versions": {},
        "measurement": measurement, "stamp": None, "summary": summary,
        "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:01:00+00:00"}))
    (d / ("answers.jsonl" if kind == "answer" else "results.jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def first_case():
    from scribe_eval.dataset import load_cases
    c = next(c for c in load_cases(DATASET / "cases.jsonl") if c.answerable and c.evidence)
    return c


@pytest.fixture
def conn():
    c = psycopg.connect(DATABASE_URL_TEST, autocommit=True)
    ids: list[str] = []
    c.ids = ids  # type: ignore[attr-defined]
    yield c
    if ids:
        c.execute("delete from eval_runs where id = any(%s::uuid[])", (ids,))
    c.close()


def test_publish_is_idempotent_labelled_seeded_and_classified(tmp_path, conn):
    case = first_case()
    rid = str(uuid.uuid4())
    conn.ids.append(rid)
    gold = {"chunk_id": "g", "doc": case.evidence[0].doc, "text": case.evidence[0].quote, "score": 0.9, "method": "dense"}
    rows = [
        {"case_id": case.id, "category": case.category, "answerable": True, "metrics": {"ndcg@10": 1.0, "mrr": 1.0},
         "ranking": [gold], "latency_ms": 12.4, "cost": None},
        {"case_id": "zz-unknown", "category": "x", "answerable": True, "metrics": {"ndcg@10": 0.0}, "ranking": [],
         "latency_ms": None, "cost": None},
    ]
    d = write_run(tmp_path, rid, rows)
    for _ in range(2):  # twice: second call replaces, never duplicates
        with conn.transaction():
            out = publish_run(conn, d, dataset_dir=DATASET, seeded=True)
    assert out["measurement"] is False and "SEEDED" in out["stamp"]
    assert conn.execute("select count(*) from eval_runs where id=%s", (rid,)).fetchone()[0] == 1
    assert conn.execute("select count(*) from eval_results where run_id=%s", (rid,)).fetchone()[0] == 2
    meta = conn.execute("select meta from eval_runs where id=%s", (rid,)).fetchone()[0]
    assert meta["workspace_kind"] == "eval" and meta["seeded"] is True and meta["measurement"] is False
    got = {r[0]: r[1] for r in conn.execute("select case_id, metrics from eval_results where run_id=%s", (rid,))}
    assert got[case.id]["_case"]["failure"] is None and got[case.id]["_case"]["question"] == case.question
    assert got["zz-unknown"]["_case"]["failure"] == "unclassified"  # unknown case id: never guessed


def test_fake_stamp_survives_publish(tmp_path, conn):
    rid = str(uuid.uuid4())
    conn.ids.append(rid)
    d = write_run(tmp_path, rid, [], kind="answer", measurement=False)
    with conn.transaction():
        publish_run(conn, d, dataset_dir=DATASET)
    meta = conn.execute("select meta from eval_runs where id=%s", (rid,)).fetchone()[0]
    assert meta["measurement"] is False and meta["stamp"] == "FAKE - NOT A MEASUREMENT" and meta["kind"] == "answer"


async def test_dashboard_queries_only_return_eval_kind_rows(tmp_path, conn):
    case = first_case()
    seeded, plain = str(uuid.uuid4()), str(uuid.uuid4())
    conn.ids += [seeded, plain]
    row = {"case_id": case.id, "category": case.category, "answerable": True, "metrics": {"ndcg@10": 0.0},
           "ranking": [], "latency_ms": 1, "cost": None}
    with conn.transaction():
        publish_run(conn, write_run(tmp_path, seeded, [row]), dataset_dir=DATASET, seeded=True)
    # a row in the same table that was NOT published for the eval dashboard (no meta) must stay invisible
    conn.execute("insert into eval_runs (id, dataset_version, split) values (%s, 'golden-v1', 'dev')", (plain,))
    conn.execute("insert into eval_results (run_id, case_id, metrics) values (%s, 'x', '{}')", (plain,))
    a = await aconnect(DATABASE_URL_TEST)
    try:
        async def q(fn, **p):
            cur = await a.execute(ts_sql(fn), p)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]

        listed = {str(r["id"]): r for r in await q("listEvalRuns", limit=200)}
        assert seeded in listed and plain not in listed
        assert listed[seeded]["case_count"] == 1 and listed[seeded]["meta"]["seeded"] is True
        assert [str(r["id"]) for r in await q("getEvalRun", runId=seeded)] == [seeded]
        assert await q("getEvalRun", runId=plain) == []
        assert [r["case_id"] for r in await q("listEvalResults", runId=seeded)] == [case.id]
        assert await q("listEvalResults", runId=plain) == []
    finally:
        await a.close()


async def test_access_rule_owner_member_and_stranger():
    a = await aconnect(DATABASE_URL_TEST)
    owner_ws = member_ws = personal_ws = None
    try:
        owner_ws = await new_workspace(a, "eval")  # owner_user_id is the generated t12-... id
        owner_id = (await (await a.execute("select owner_user_id from workspaces where id=%s", (owner_ws,))).fetchone())[0]
        member_ws = await new_workspace(a, "eval")
        personal_ws = await new_workspace(a, "personal")
        pers_owner = (await (await a.execute("select owner_user_id from workspaces where id=%s", (personal_ws,))).fetchone())[0]
        await a.execute("insert into workspace_members(workspace_id, user_id, role) values (%s,'member-user','member')", (member_ws,))

        async def can(user):
            cur = await a.execute(ts_sql("canViewEval"), {"userId": user})
            return bool(await cur.fetchall())

        assert await can(owner_id)
        assert await can("member-user")
        assert not await can("stranger")
        assert not await can(pers_owner)  # owning a personal workspace is not enough
        assert not await can("")
    finally:
        purge_workspaces(DATABASE_URL_TEST, [w for w in (owner_ws, member_ws, personal_ws) if w])
        await a.close()

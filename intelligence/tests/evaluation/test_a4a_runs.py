"""A4a: dataset/corpus flags, workspace-per-dataset, test-split lock, provenance and spend refusals through the real
`run-profile` / `ingest-corpus` / `answer` entry points. Offline: embeddings are the hash-vector FakeEmbedder (injected
via `run_profile.build_embedder`), nothing reaches a provider. The DB is used only for what already used it."""
from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import DATABASE_URL_TEST, needs_db

from scribe_eval import cost
from scribe_eval import ingest_corpus as ic
from scribe_eval import run_profile as rp
from scribe_eval.cli import main as cli_main
from scribe_eval.commands import register_all
from scribe_eval.scoring import score_split

DOCS = {
    "alpha.md": "# Alpha\n\nThe zebra protocol requires quarterly review of every ledger entry.\n",
    "beta.md": "# Beta\n\nThe walrus procedure forbids unsigned changes to the archive.\n",
}


def case_row(cid, split="dev", reviewed=True, question="What does the zebra protocol require?", quote="zebra protocol"):
    return {"id": cid, "split": split, "category": "single-fact", "question": question, "answerable": True,
            "reference_answer": "quarterly review", "reviewed": reviewed,
            "evidence": [{"doc": "alpha.md", "version": 1, "quote": quote, "grade": 1}],
            "forbidden": [{"doc": "beta.md"}]}


def make_dataset(tmp_path: Path, cases: list[dict], name: str | None = None) -> Path:
    d = tmp_path / (name or f"a4a-{uuid.uuid4().hex[:8]}")
    d.mkdir()
    (d / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    return d


def _compete_for_test_lock(runs, barrier, milestone_id, result_queue):
    from scribe_eval.run_profile import RunProfileError, acquire_test_lock

    barrier.wait()
    try:
        acquire_test_lock(runs, "ds", "a" * 64, ["profile"], milestone_id, "abc123")
        result_queue.put((milestone_id, "acquired"))
    except RunProfileError:
        result_queue.put((milestone_id, "refused"))


def _hold_execution_guard(runs, ready_queue, release_event, milestone_id):
    from scribe_eval.run_profile import acquire_test_lock, test_split_execution_guard

    with test_split_execution_guard(runs, "ds"):
        acquire_test_lock(runs, "ds", "a" * 64, ["profile"], milestone_id, "abc123")
        ready_queue.put("held")
        release_event.wait(timeout=10)


def ingest(dataset: Path, docs=DOCS) -> str:
    from _db import aconnect
    from scribe_ingest.embedding import FakeEmbedder

    owner = ic.eval_owner(dataset)

    async def go() -> str:
        conn = await aconnect(DATABASE_URL_TEST)
        try:
            wid = await ic.ensure_eval_workspace(conn, owner)
            res = await ic.ingest_corpus(conn, wid, list(docs), lambda n: docs[n].encode(), FakeEmbedder(), owner=owner)
            assert all(r.status == "ACTIVE" for _, r in res)
            return wid
        finally:
            await conn.close()

    return asyncio.run(go())


def rp_args(tmp_path, dataset, **over):
    base = dict(split="dev", profile="lexical-only", profiles=None, database_url=DATABASE_URL_TEST, dataset=str(dataset),
                corpus="fixtures/corpus-x", out=str(tmp_path / "rank.jsonl"), cache_path=str(tmp_path / "c.sqlite"),
                limit=0, milestone_report=False, milestone_id=None, runs_dir=str(tmp_path / "runs"), spend_cap=None,
                ledger=str(tmp_path / "ledger.jsonl"))
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    from scribe_ingest.embedding import FakeEmbedder

    monkeypatch.setattr(rp, "build_embedder", lambda cache: FakeEmbedder())


def read(path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------- no DB: flags, defaults, workspace scheme

def _parsers():
    parser = argparse.ArgumentParser()
    register_all(parser.add_subparsers(dest="command"))
    return parser


def test_defaults_keep_golden_v1_and_fixtures_corpus():
    p = _parsers()
    ing = p.parse_args(["ingest-corpus"])
    assert (ing.dataset, ing.corpus, ing.manifest) == ("benchmarks/datasets/golden-v1", "fixtures/corpus", None)
    r = p.parse_args(["run-profile", "--profile", "hybrid-rrf", "--split", "dev", "--out", "x"])
    assert (r.dataset, r.corpus, r.profiles, r.milestone_report, r.milestone_id) == (
        "benchmarks/datasets/golden-v1", "fixtures/corpus", None, False, None)
    s = p.parse_args(["score", "--rankings", "r", "--split", "dev"])
    assert (s.dataset, s.corpus) == ("benchmarks/datasets/golden-v1", "fixtures/corpus")
    a = p.parse_args(["answer", "--rankings", "r", "--split", "dev"])
    assert (a.dataset, a.corpus, a.spend_cap) == ("benchmarks/datasets/golden-v1", "fixtures/corpus", None)
    for sub in (ing, r, a):
        assert sub.spend_cap is None and sub.ledger == "benchmarks/spend-ledger.jsonl"


def test_workspace_owner_is_derived_from_the_dataset_name():
    assert ic.eval_owner("benchmarks/datasets/golden-v1") == ic.EVAL_OWNER == "eval-golden-v1"  # unchanged
    assert ic.eval_owner(ic.DEFAULT_DATASET) == "eval-golden-v1"
    assert ic.eval_owner("benchmarks/datasets/gov-v1/") == "eval-gov-v1"
    assert ic.eval_owner("gov-v1") == "eval-gov-v1"
    assert ic.eval_owner("/x/My Set!") == "eval-my-set"
    assert ic.eval_owner("a/gov-v1") != ic.eval_owner("a/golden-v1")


def test_manifest_maps_original_names_to_ingest_files_and_refuses_missing(tmp_path):
    corpus = tmp_path / "corpus-gov"
    corpus.mkdir()
    for f in ("nist.pdf", "scan.raster.pdf", "plain.pdf"):
        (corpus / f).write_bytes(b"%PDF-1.4\n")
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"documents": [
        {"id": "nist", "ingest_file": "nist.pdf"}, {"id": "scan", "ingest_file": "scan.raster.pdf"}, {"id": "plain"}]}))
    assert ic.load_manifest(man, corpus) == {"nist.pdf": "nist.pdf", "plain.pdf": "plain.pdf", "scan.pdf": "scan.raster.pdf"}
    (corpus / "scan.raster.pdf").unlink()
    with pytest.raises(ic.CliError, match="scan.raster.pdf"):
        ic.load_manifest(man, corpus)
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(ic.CliError, match="unreadable manifest"):
        ic.load_manifest(bad, corpus)


def test_ingest_cli_refuses_a_manifest_with_missing_files_before_touching_anything(tmp_path, capsys):
    corpus = tmp_path / "c"
    corpus.mkdir()
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"documents": [{"id": "gone", "ingest_file": "gone.raster.pdf"}]}))
    rc = cli_main(["ingest-corpus", "--corpus", str(corpus), "--manifest", str(man), "--embedder", "fake",
                   "--database-url", "postgresql://nobody@localhost:1/x_test", "--dataset", str(tmp_path / "ds-x")])
    assert rc == 2 and "gone.raster.pdf" in capsys.readouterr().err


def test_ingest_spend_cap_refuses_a_real_embedder_run_before_any_provider_call(tmp_path, capsys, monkeypatch):
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "a.md").write_text("word " * 2000)
    led = tmp_path / "l.jsonl"
    cost.append_spend(run_id="r", kind="x", model_ids=[], tokens={}, usd=10.0, estimated=False, live_calls=1, path=led)
    monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
    base = ["ingest-corpus", "--corpus", str(corpus), "--database-url", "postgresql://nobody@localhost:1/x_test",
            "--ledger", str(led)]
    assert cli_main(base) == 2 and "spend cap" in capsys.readouterr().err
    assert cli_main(base + ["--spend-cap", "11"]) == 2 and "raise the cap" in capsys.readouterr().err
    assert ic.estimate_ingest_usd({"a.md": corpus / "a.md"}) > 0


def test_ingest_estimate_counts_ocr_for_raster_copies(tmp_path):
    plain, raster = tmp_path / "x.pdf", tmp_path / "x.raster.pdf"
    body = b"%PDF-1.4\n" + b"<< /Type /Page >>\n" * 40
    plain.write_bytes(body)
    raster.write_bytes(body)
    assert ic.estimate_ingest_usd({"x.pdf": raster}) > ic.estimate_ingest_usd({"x.pdf": plain}) > 0


def test_lock_unit_rules(tmp_path):
    runs = tmp_path / "runs"
    ds = make_dataset(tmp_path, [case_row("t1", "test")])
    sha = rp.dataset_sha256(ds)
    assert rp.acquire_test_lock(runs, "ds", sha, ["a", "b"], "m1", "abc123") == ["a", "b"]
    lock = json.loads(rp.lock_path(runs, "ds").read_text())
    assert lock["dataset_sha256"] == sha and lock["entries"]["a"]["commit"] == "abc123"
    assert lock["entries"]["a"]["started_at"] and lock["milestones"]["m1"]["profiles"] == ["a", "b"]
    rp.mark_profile_complete(runs, "ds", "a", tmp_path / "a.jsonl")
    assert rp.acquire_test_lock(runs, "ds", sha, ["a", "b"], "m1", "abc123") == ["b"]  # resume: a is done
    with pytest.raises(rp.RunProfileError, match="INV-12"):
        rp.acquire_test_lock(runs, "ds", sha, ["a"], "m2", "abc123")
    with pytest.raises(rp.RunProfileError, match="profile set"):
        rp.acquire_test_lock(runs, "ds", sha, ["a"], "m1", "abc123")
    with pytest.raises(rp.RunProfileError, match="changed"):
        rp.acquire_test_lock(runs, "ds", "0" * 64, ["c"], "m3", "abc123")
    assert rp.acquire_test_lock(runs, "ds", sha, ["c"], "m3", "abc123") == ["c"]  # a never-run profile is fine


def test_concurrent_milestones_cannot_both_acquire_same_profile(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    result_queue = ctx.Queue()
    runs = str(tmp_path / "runs")
    processes = [ctx.Process(target=_compete_for_test_lock,
                             args=(runs, barrier, milestone_id, result_queue))
                 for milestone_id in ("m1", "m2")]
    for process in processes:
        process.start()
    results = [result_queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sorted(status for _, status in results) == ["acquired", "refused"]


def test_execution_guard_rejects_overlap_and_recovers_after_owner_exit(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready_queue = ctx.Queue()
    release_event = ctx.Event()
    runs = str(tmp_path / "runs")
    owner = ctx.Process(target=_hold_execution_guard, args=(runs, ready_queue, release_event, "m1"))
    owner.start()
    assert ready_queue.get(timeout=10) == "held"
    assert rp.acquire_test_lock(runs, "ds", "a" * 64, ["profile"], "m1", "abc123") == ["profile"]

    with pytest.raises(rp.RunProfileError, match="already in progress"):
        with rp.test_split_execution_guard(runs, "ds"):
            pytest.fail("overlapping test-split execution acquired the guard")

    release_event.set()
    owner.join(timeout=10)
    assert owner.exitcode == 0
    with rp.test_split_execution_guard(runs, "ds"):
        assert rp.acquire_test_lock(runs, "ds", "a" * 64, ["profile"], "m1", "abc123") == ["profile"]


def test_metered_embedder_separates_live_from_cached_calls():
    class Cachey:
        model, is_fake, dimensions = "gemini-embedding-001", False, 4

        def __init__(self):
            self.texts_embedded, self.seen = 0, set()

        async def embed_queries(self, texts):
            for t in texts:
                if t not in self.seen:
                    self.seen.add(t)
                    self.texts_embedded += 1
            return [[0.1] * 4 for _ in texts]

    m = cost.MeteredEmbedder(Cachey())

    async def go():
        await m.embed_queries(["what is x"])
        first = (m.last_live, m.last_ms >= 0)
        await m.embed_queries(["what is x"])
        return first, m.last_live

    first, second_live = asyncio.run(go())
    assert first == (True, True) and second_live is False
    assert m.texts == 2 and m.live_texts == 1 and m.usage().cached is False and m.usage().input_tokens > 0


# ---------------------------------------------------------------- DB: end to end through run-profile

@needs_db
def test_rankings_carry_provenance_workspace_and_timing(tmp_path):
    ds = make_dataset(tmp_path, [case_row("d1")])
    wid = ingest(ds, {"alpha.md": DOCS["alpha.md"]})
    args = rp_args(tmp_path, ds, profile="hybrid-rrf")
    assert rp.run(args) == 0
    (rec,) = read(args.out)
    assert rec["ranking"] and all(i["workspace_id"] == wid and i["source_deleted"] is False and i["source_id"]
                                  for i in rec["ranking"])
    meta = rec["meta"]
    assert (meta["workspace_id"], meta["workspace_owner"], meta["dataset"], meta["corpus"]) == (
        wid, ic.eval_owner(ds), ds.name, "fixtures/corpus-x")
    assert rec["embed_ms"] >= 0 and rec["retrieval_ms"] > 0 and rec["embed_cached"] is False
    assert rec["latency_ms"] == pytest.approx(rec["embed_ms"] + rec["retrieval_ms"], abs=0.2)
    assert rec["cost"]["usd"] == 0.0 and rec["cost"]["tokens"]["embed_input"] > 0  # fake model: free, still counted
    side = json.loads(Path(str(args.out) + ".meta.json").read_text())
    assert side["latency"]["embed_live_n"] == 1 and side["profile"] == "hybrid-rrf"
    # scoring picks the workspace up from the record: clean, verified, and a wrong expected workspace is a decoy leak
    _, results, summary = score_split(ds, "dev", Path(args.out))
    assert summary["leak"]["leak_rate"] == 0.0 and summary["leak"]["leak_rate_verified"] is True
    _, bad, bad_summary = score_split(ds, "dev", Path(args.out), expected_workspace_id=str(uuid.uuid4()))
    assert bad_summary["leak"]["leak_rate"] == 1.0 and bad[0].leak["reasons"] == ["decoy_workspace"]


@needs_db
def test_forbidden_doc_in_a_real_run_leaks_end_to_end(tmp_path):
    ds = make_dataset(tmp_path, [case_row("d1")])  # forbids beta.md
    ingest(ds)  # both documents are indexed, so beta.md is retrievable
    args = rp_args(tmp_path, ds, profile="hybrid-rrf")
    assert rp.run(args) == 0
    _, results, summary = score_split(ds, "dev", Path(args.out))
    assert {i["doc"] for i in read(args.out)[0]["ranking"]} == {"alpha.md", "beta.md"}
    assert results[0].leak["reasons"] == ["forbidden_doc"] and summary["leak"]["leak_rate"] == 1.0


@needs_db
def test_each_dataset_has_its_own_workspace_and_runs_never_cross(tmp_path):
    a = make_dataset(tmp_path, [case_row("d1")])
    b = make_dataset(tmp_path, [case_row("d1", question="What does the walrus procedure forbid?")])
    wa, wb = ingest(a, {"alpha.md": DOCS["alpha.md"]}), ingest(b, {"beta.md": DOCS["beta.md"]})
    assert wa != wb
    for ds, wid, expect_doc in ((a, wa, "alpha.md"), (b, wb, "beta.md")):
        args = rp_args(tmp_path, ds, out=str(tmp_path / f"{ds.name}.jsonl"))
        assert rp.run(args) == 0
        items = read(args.out)[0]["ranking"]
        assert {i["doc"] for i in items} == {expect_doc} and {i["workspace_id"] for i in items} == {wid}


@needs_db
def test_run_profile_without_an_ingested_workspace_is_refused(tmp_path, capsys):
    ds = make_dataset(tmp_path, [case_row("d1")])
    assert rp.run(rp_args(tmp_path, ds)) == 2 and "ingest-corpus" in capsys.readouterr().err


@needs_db
async def test_provenance_lookup_flags_foreign_workspace_and_soft_deleted_source(tmp_path):
    from _db import aconnect
    from scribe_ingest.embedding import FakeEmbedder

    conn = await aconnect(DATABASE_URL_TEST)
    wids = []
    for label in ("own", "decoy"):
        cur = await conn.execute("insert into workspaces(kind, owner_user_id) values ('eval', %s) returning id",
                                 (f"a4a-prov-{label}-{uuid.uuid4().hex[:6]}",))
        wid = str((await cur.fetchone())[0])
        wids.append(wid)
        await ic.ingest_corpus(conn, wid, ["alpha.md"], lambda n: DOCS[n].encode(), FakeEmbedder())
    chunks = {}
    for wid in wids:
        cur = await conn.execute("select id::text from chunks where workspace_id = %s", (wid,))
        chunks[wid] = [r[0] for r in await cur.fetchall()]
    prov = await rp.lookup_provenance(conn, chunks[wids[0]] + chunks[wids[1]])
    assert {prov[c]["workspace_id"] for c in chunks[wids[1]]} == {wids[1]} and not any(p["source_deleted"] for p in prov.values())
    await conn.execute("update files set deleted_at = now() where workspace_id = %s", (wids[0],))
    prov = await rp.lookup_provenance(conn, chunks[wids[0]])
    assert all(p["source_deleted"] is True for p in prov.values())
    # and those rows score as leaks
    from scribe_eval.metrics import case_leak
    from scribe_eval.dataset import Case

    case = Case.model_validate(case_row("x"))
    rows = [{"chunk_id": c, "doc": "alpha.md", **prov[c]} for c in chunks[wids[0]]]
    assert case_leak(case, rows, wids[0])["reasons"] == ["soft_deleted_source"]
    assert case_leak(case, rows, wids[1])["reasons"] == ["decoy_workspace", "soft_deleted_source"]
    await conn.close()


@needs_db
def test_multiple_profiles_in_one_run_write_one_file_each(tmp_path):
    ds = make_dataset(tmp_path, [case_row("d1")])
    ingest(ds)
    out = tmp_path / "multi"
    args = rp_args(tmp_path, ds, profile=None, profiles="lexical-only,hybrid-rrf", out=str(out))
    assert rp.run(args) == 0
    assert sorted(p.name for p in out.glob("*.jsonl")) == ["hybrid-rrf.jsonl", "lexical-only.jsonl"]
    assert read(out / "lexical-only.jsonl")[0]["embed_cached"] is None  # lexical: no embedding
    assert read(out / "hybrid-rrf.jsonl")[0]["meta"]["profile"] == "hybrid-rrf"
    assert rp.run(rp_args(tmp_path, ds, profile="lexical-only", profiles="hybrid-rrf")) == 2  # both flags
    assert rp.run(rp_args(tmp_path, ds, profile=None, profiles=None)) == 2  # neither


# ---------------------------------------------------------------- DB: test-split once-only lock

@needs_db
def test_test_split_milestone_lock(tmp_path, capsys):
    ds = make_dataset(tmp_path, [case_row("t1", "test"), case_row("d1", "dev")])
    ingest(ds)

    def go(**kw):
        over = dict(split="test", milestone_report=True, milestone_id="m1")
        over.update(kw)
        return rp.run(rp_args(tmp_path, ds, **over))

    # refusals that must not create a lock
    assert go(milestone_report=False) == 2 and "INV-12" in capsys.readouterr().err
    assert go(milestone_id=None) == 2 and "--milestone-id" in capsys.readouterr().err
    assert go(limit=1) == 2 and "--limit" in capsys.readouterr().err
    lock = rp.lock_path(tmp_path / "runs", ds.name)
    assert not lock.exists()
    # first run is allowed and recorded
    assert go() == 0
    rec = json.loads(lock.read_text())
    assert rec["dataset_sha256"] == rp.dataset_sha256(ds) and rec["entries"]["lexical-only"]["status"] == "completed"
    assert rec["entries"]["lexical-only"]["milestone_id"] == "m1" and rec["entries"]["lexical-only"]["commit"]
    assert rec["milestones"]["m1"]["profiles"] == ["lexical-only"] and rec["milestones"]["m1"]["created_at"]
    out = tmp_path / "rank.jsonl"
    assert [r["case_id"] for r in read(out)] == ["t1"] and read(out)[0]["meta"]["milestone_id"] == "m1"
    capsys.readouterr()
    # a second run of the same dataset and profile is refused, with another milestone id and with none matching
    assert go(milestone_id="m2", out=str(tmp_path / "second.jsonl")) == 2
    err = capsys.readouterr().err
    assert "INV-12" in err and "m1" in err and not (tmp_path / "second.jsonl").exists()
    # the same milestone id is an idempotent resume: nothing is re-run
    before = out.read_text()
    assert go() == 0 and "already completed" in capsys.readouterr().out and out.read_text() == before


@needs_db
def test_interrupted_milestone_resumes_with_same_id(tmp_path):
    ds = make_dataset(tmp_path, [case_row("t1", "test")])
    ingest(ds)
    runs = tmp_path / "runs"
    rp.acquire_test_lock(runs, ds.name, rp.dataset_sha256(ds), ["lexical-only"], "m9", "deadbeef")  # crashed after start
    args = rp_args(tmp_path, ds, split="test", milestone_report=True, milestone_id="m9")
    assert rp.run(rp_args(tmp_path, ds, split="test", milestone_report=True, milestone_id="other")) == 2
    assert rp.run(args) == 0
    assert json.loads(rp.lock_path(runs, ds.name).read_text())["entries"]["lexical-only"]["status"] == "completed"


@needs_db
def test_test_split_refuses_unreviewed_cases_and_leaves_no_lock(tmp_path, capsys):
    ds = make_dataset(tmp_path, [case_row("t1", "test"), case_row("t2", "test", reviewed=False)])
    ingest(ds)
    rc = rp.run(rp_args(tmp_path, ds, split="test", milestone_report=True, milestone_id="m1"))
    err = capsys.readouterr().err
    assert rc == 2 and "t2" in err and "not human-reviewed" in err
    assert not rp.lock_path(tmp_path / "runs", ds.name).exists() and not (tmp_path / "rank.jsonl").exists()


@needs_db
def test_dataset_changed_after_lock_is_refused(tmp_path, capsys):
    ds = make_dataset(tmp_path, [case_row("t1", "test")])
    ingest(ds)
    kw = dict(split="test", milestone_report=True, milestone_id="m1")
    assert rp.run(rp_args(tmp_path, ds, **kw)) == 0
    (ds / "cases.jsonl").write_text((ds / "cases.jsonl").read_text() + json.dumps(case_row("t2", "test")) + "\n")
    assert rp.run(rp_args(tmp_path, ds, profile="hybrid-rrf", **kw)) == 2 and "changed" in capsys.readouterr().err


# ---------------------------------------------------------------- DB: spend ledger through run-profile

class PricedFake:
    """FakeEmbedder that claims a priced model id, so the spend path is exercised without any provider."""

    def __new__(cls, *a, **k):
        from scribe_ingest.embedding import FakeEmbedder

        e = FakeEmbedder()
        e.model = "gemini-embedding-001"
        e.is_fake = False
        return e


@needs_db
def test_run_profile_records_spend_and_refuses_over_cap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rp, "build_embedder", lambda cache: PricedFake())
    ds = make_dataset(tmp_path, [case_row("d1")])
    ingest(ds)
    args = rp_args(tmp_path, ds, profile="hybrid-rrf")
    assert rp.run(args) == 0
    (line,) = [json.loads(l) for l in Path(args.ledger).read_text().splitlines()]
    assert line["kind"] == "run-profile" and line["usd"] > 0 and line["estimated"] is True
    assert line["tokens"]["gemini-embedding-001:input"] > 0 and line["live_calls"] == 1 and line["cached"] is False
    assert cost.spend_total(args.ledger) == pytest.approx(line["usd"])
    # a lowered cap below ledger + estimate refuses, writes no rankings, and appends nothing
    args2 = rp_args(tmp_path, ds, profile="hybrid-rrf", spend_cap=line["usd"], out=str(tmp_path / "r2.jsonl"))
    assert rp.run(args2) == 2 and "spend cap" in capsys.readouterr().err
    assert not Path(args2.out).exists() and len(Path(args.ledger).read_text().splitlines()) == 1
    # raising the cap is refused
    assert rp.run(rp_args(tmp_path, ds, profile="hybrid-rrf", spend_cap=10.5, out=str(tmp_path / "r3.jsonl"))) == 2
    assert "raise the cap" in capsys.readouterr().err
    # the cap itself: a ledger already at $10 blocks any provider-calling run
    Path(args.ledger).write_text("")
    cost.append_spend(run_id="x", kind="x", model_ids=[], tokens={}, usd=10.0, estimated=False, live_calls=1, path=args.ledger)
    assert rp.run(rp_args(tmp_path, ds, profile="hybrid-rrf", out=str(tmp_path / "r4.jsonl"))) == 2
    assert not (tmp_path / "r4.jsonl").exists()


@needs_db
def test_replay_mode_estimates_zero_and_lexical_profile_costs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "build_embedder", lambda cache: PricedFake())
    assert rp.estimate_run_usd(["hybrid-rrf"], ["q " * 50], "gemini-embedding-001", replay=True) == 0.0
    assert rp.estimate_run_usd(["lexical-only"], ["q " * 50], "gemini-embedding-001", replay=False) == 0.0
    assert rp.estimate_run_usd(["hybrid-rrf"], ["q " * 50], "gemini-embedding-001", replay=False) > 0.0


# ---------------------------------------------------------------- answer: leak, citation validity, spend refusal

def _answer_inputs(tmp_path, ranking_items):
    ds = make_dataset(tmp_path, [case_row("d1")])
    rk = tmp_path / "rk.jsonl"
    rk.write_text(json.dumps({"case_id": "d1", "ranking": ranking_items, "meta": {"workspace_id": "W"}}) + "\n")
    return ds, rk


def _item(doc, text="The zebra protocol requires quarterly review.", ws="W", deleted=False):
    return {"chunk_id": "c1", "doc": doc, "text": text, "score": 0.9, "method": "hybrid", "workspace_id": ws,
            "source_deleted": deleted}


@pytest.mark.parametrize("doc,ws,deleted,leaked", [
    ("alpha.md", "W", False, 0.0), ("beta.md", "W", False, 1.0), ("alpha.md", "OTHER", False, 1.0),
    ("alpha.md", "W", True, 1.0)])
def test_answer_leak_follows_the_cited_item(tmp_path, doc, ws, deleted, leaked):
    from scribe_eval import answer as ans
    from scribe_eval.dataset import load_cases

    ds, rk = _answer_inputs(tmp_path, [_item(doc, ws=ws, deleted=deleted)])
    cases = load_cases(ds / "cases.jsonl")
    rankings = ans.load_rankings_lenient(rk)
    results, _ = ans.run_answers(cases, rankings, ans.FakeGenerator(), ans.FakeVerifier(), system="s")
    (r,) = results
    assert r.cited_labels or r.claims  # FakeGenerator cites [E1]
    assert r.metrics["leak"] == leaked and r.leak["leaked"] is bool(leaked)
    assert r.metrics["citation_validity"] == 1.0 and r.citation_validity["valid"] == r.citation_validity["total"] >= 1
    summary = ans.summarize_answers(results)
    assert summary["leak"]["leak_rate"] == leaked and summary["citation_validity"]["rate"] == 1.0


def test_answer_citation_to_a_label_outside_the_bundle_is_invalid(tmp_path):
    from scribe_eval import answer as ans
    from scribe_eval.dataset import load_cases

    ds, rk = _answer_inputs(tmp_path, [_item("alpha.md")])
    case = load_cases(ds / "cases.jsonl")[0]
    bundle = ans.build_bundle(ans.load_rankings_lenient(rk)["d1"])
    outcomes, _ = ans.verify_claims_local(["It requires review [E1].", "It also needs sign off [E9]."], bundle, None)
    res = ans.score_case(case, bundle, "It requires review [E1]. It also needs sign off [E9].", outcomes)
    assert res.citation_validity["total"] == 2 and res.citation_validity["valid"] == 1
    assert [f["code"] for f in res.citation_validity["failures"]] == ["label_not_in_turn"]
    assert res.metrics["citation_validity"] == 0.5


def test_answer_real_generator_is_refused_over_the_spend_cap_before_any_call(tmp_path, monkeypatch, capsys):
    ds, rk = _answer_inputs(tmp_path, [_item("alpha.md")])
    led = tmp_path / "l.jsonl"
    cost.append_spend(run_id="r", kind="x", model_ids=[], tokens={}, usd=10.0, estimated=False, live_calls=1, path=led)
    monkeypatch.setenv("GOOGLE_GENERATIVE_AI_API_KEY", "dummy-not-a-key")
    import httpx

    def boom(*a, **k):  # any provider traffic would raise
        raise AssertionError("provider was called")

    monkeypatch.setattr(httpx.Client, "post", boom)
    rc = cli_main(["answer", "--rankings", str(rk), "--dataset", str(ds), "--split", "dev", "--generator", "real",
                   "--verifier", "none", "--ledger", str(led), "--out", str(tmp_path / "o")])
    assert rc == 2 and "spend cap" in capsys.readouterr().err and not (tmp_path / "o").exists()
    rc = cli_main(["answer", "--rankings", str(rk), "--dataset", str(ds), "--split", "dev", "--generator", "real",
                   "--verifier", "none", "--ledger", str(led), "--spend-cap", "99", "--out", str(tmp_path / "o")])
    assert rc == 2 and "raise the cap" in capsys.readouterr().err


def test_answer_with_fakes_never_touches_the_ledger_and_records_leak_in_the_run(tmp_path):
    ds, rk = _answer_inputs(tmp_path, [_item("beta.md")])
    led = tmp_path / "l.jsonl"
    out = tmp_path / "o"
    rc = cli_main(["answer", "--rankings", str(rk), "--dataset", str(ds), "--split", "dev", "--generator", "fake",
                   "--verifier", "fake", "--ledger", str(led), "--out", str(out), "--corpus", "fixtures/corpus-x"])
    assert rc == 0 and not led.exists()
    run = json.loads(next(out.glob("*/run.json")).read_text())
    assert run["leak_rate"] == 1.0 and run["corpus"] == "fixtures/corpus-x" and run["dataset_name"] == ds.name
    assert run["workspace_id"] == "W"


# ---------------------------------------------------------------- DB: ingest-corpus CLI and score run record

@needs_db
def test_ingest_corpus_cli_uses_the_dataset_workspace_and_score_records_the_new_fields(tmp_path):
    corpus = tmp_path / "corp"
    corpus.mkdir()
    for n, t in DOCS.items():
        (corpus / n).write_text(t)
    ds = make_dataset(tmp_path, [case_row("d1")])
    rc = cli_main(["ingest-corpus", "--corpus", str(corpus), "--dataset", str(ds), "--embedder", "fake",
                   "--database-url", DATABASE_URL_TEST, "--cache-path", str(tmp_path / "c.sqlite")])
    assert rc == 0
    import psycopg

    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        rows = c.execute("select id::text, kind from workspaces where owner_user_id = %s", (ic.eval_owner(ds),)).fetchall()
        assert len(rows) == 1 and rows[0][1] == "eval"
        wid = rows[0][0]
        assert {r[0] for r in c.execute("select name from files where workspace_id = %s", (wid,)).fetchall()} == set(DOCS)
    args = rp_args(tmp_path, ds, profile="hybrid-rrf")
    assert rp.run(args) == 0
    out = tmp_path / "runs-out"
    assert cli_main(["score", "--rankings", args.out, "--dataset", str(ds), "--split", "dev", "--profile", "hybrid-rrf",
                     "--out", str(out), "--corpus", str(corpus)]) == 0
    run = json.loads(next(out.glob("*/run.json")).read_text())
    assert run["workspace_id"] == wid and run["dataset_name"] == ds.name and run["corpus"] == str(corpus)
    assert run["leak_rate"] == 1.0 and run["leak_rate_verified"] is True  # beta.md is forbidden and was ingested
    assert run["latency"]["total_p50_ms"] > 0 and run["latency"]["embed_live_n"] == 1
    assert run["cost"]["queries"] == 1 and run["summary"]["leak"]["leaked_case_ids"] == ["d1"]
    row = json.loads(next(out.glob("*/results.jsonl")).read_text().splitlines()[0])
    assert row["leak"]["leaked"] is True and row["timing"]["embed_cached"] is False

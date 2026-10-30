"""T31 retrieval regression gate. Everything here is SEEDED (synthetic corpus, hash-vector embeddings, token-overlap
reranker): it proves the machinery, not retrieval quality. No test touches a provider."""
from __future__ import annotations

import copy
import gzip
import json
import os
from pathlib import Path

import psycopg
import pytest
from conftest import DATABASE_URL_TEST, needs_db

import _gate_seed as seed
from scribe_eval import gate
from scribe_eval.cli import main as cli_main


# ------------------------------------------------------------------ pure logic (no DB)

def doc(recall, mrr, *, hits=0, missed=0, unans=3, **over):
    cases = {f"c{i}": {"answerable": True, "metrics": {"recall@5": r, "mrr": m}} for i, (r, m) in enumerate(zip(recall, mrr))}
    for j in range(unans):
        cases[f"u{j}"] = {"answerable": False, "missed_abstention": j < missed, "top_score": 0.1}
    d = {"format": 1, "kind": "retrieval-gate-result", "split": "validation", "profile": "p", "dataset_sha256": "x",
         "profile_config_hash": "config", "models": {"embedding": "embed", "rerank": "rerank"},
         "dataset_reviewed": True, "failed_docs": [], "cross_workspace_hits": hits, "tau": 0.5, "tau_calibrated": False, "degraded_cases": 0,
         "summary": {"unanswerable": unans}, "cases": cases, "seeded": False, "measurement": True}
    d.update(over)
    return d


def levels(rep):
    return {(c.rule, c.name): c.level for c in rep.checks}


R = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0, 1.0] * 4  # 40 cases


def test_unchanged_passes():
    rep = gate.compare(doc(R, R), doc(R, R), n_resamples=2000)
    assert rep.passed and all(c.level == "pass" for c in rep.checks)


def test_improvement_passes():
    better = [min(1.0, x + 0.5) for x in R]
    assert gate.compare(doc(R, R), doc(better, better), n_resamples=2000).passed


def test_consistent_drop_fails_r1_for_both_metrics():
    worse = [x * 0.5 for x in R]
    rep = gate.compare(doc(R, R), doc(worse, worse), n_resamples=2000)
    assert not rep.passed
    lv = levels(rep)
    assert lv[("R1", "recall@5 not worse")] == "fail" and lv[("R1", "mrr not worse")] == "fail"
    assert "upper bound < 0" in next(c.detail for c in rep.checks if c.rule == "R1")


def test_drop_within_noise_warns_but_passes():
    one_off = list(R)
    one_off[0] = 0.0  # one case flips out of 40: mean drops, interval contains 0
    rep = gate.compare(doc(R, R), doc(one_off, R), n_resamples=2000)
    lv = levels(rep)
    assert rep.passed and lv[("R1", "recall@5 not worse")] == "warn" and lv[("R1", "mrr not worse")] == "pass"


def test_r1_is_exactly_ci_upper_bound_below_zero():
    # the rule and `scribe-eval compare` share one implementation: fail <=> paired_bootstrap(...).hi < 0
    from scribe_eval.bootstrap import paired_bootstrap

    cur = [x * 0.5 for x in R]
    res = paired_bootstrap({f"c{i}": v for i, v in enumerate(R)}, {f"c{i}": v for i, v in enumerate(cur)}, n_resamples=2000)
    rep = gate.compare(doc(R, R), doc(cur, R), n_resamples=2000)
    assert (res.hi < 0) == (levels(rep)[("R1", "recall@5 not worse")] == "fail")


def test_any_cross_workspace_hit_fails_r2():
    rep = gate.compare(doc(R, R), doc(R, R, hits=1), n_resamples=500)
    assert not rep.passed and levels(rep)[("R2", "cross-workspace hits == 0")] == "fail"


def test_missed_abstention_increase_fails_r3_equal_or_fewer_pass():
    assert not gate.compare(doc(R, R, missed=1), doc(R, R, missed=2), n_resamples=500).passed
    assert gate.compare(doc(R, R, missed=2), doc(R, R, missed=2), n_resamples=500).passed
    assert gate.compare(doc(R, R, missed=2), doc(R, R, missed=0), n_resamples=500).passed


@pytest.mark.parametrize("field,value", [("dataset_sha256", "y"), ("profile", "q"), ("split", "dev"),
    ("profile_config_hash", "different"), ("models", {"embedding": "other", "rerank": "rerank"}),
    ("tau", 0.9), ("tau_calibrated", True)])
def test_precondition_mismatch_fails(field, value):
    rep = gate.compare(doc(R, R), doc(R, R, **{field: value}), n_resamples=500)
    assert not rep.passed and any(c.rule == "P" and c.level == "fail" for c in rep.checks)


def test_newly_unindexable_document_fails_but_known_failure_is_fine():
    assert gate.compare(doc(R, R, failed_docs=["scan.pdf"]), doc(R, R, failed_docs=["scan.pdf"]), n_resamples=500).passed
    rep = gate.compare(doc(R, R, failed_docs=["scan.pdf"]), doc(R, R, failed_docs=["scan.pdf", "a.pdf"]), n_resamples=500)
    assert not rep.passed


def test_case_id_mismatch_fails():
    cur = doc(R, R)
    cur["cases"].pop("c0")
    assert not gate.compare(doc(R, R), cur, n_resamples=500).passed


def write(path: Path, d: dict) -> Path:
    path.write_text(json.dumps(d), encoding="utf-8")
    return path


def test_arming_status(tmp_path):
    assert gate.arming_status(tmp_path / "none.json")[0] is False
    seeded = write(tmp_path / "s.json", doc(R, R, seeded=True, measurement=False, stamp="SEEDED"))
    assert gate.arming_status(seeded)[0] is False and gate.arming_status(seeded, accept_seeded=True)[0] is True
    fake = write(tmp_path / "f.json", doc(R, R, seeded=False, measurement=False, stamp="FAKE"))
    assert gate.arming_status(fake)[0] is False
    assert gate.arming_status(write(tmp_path / "r.json", doc(R, R)))[0] is True
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(gate.GateError):
        gate.arming_status(tmp_path / "bad.json")


def test_committed_seeded_baseline_is_stamped_and_small():
    path = seed.SEEDED_BASELINE
    d = json.loads(path.read_text())
    assert d["seeded"] is True and d["measurement"] is False and "NOT A MEASUREMENT" in d["stamp"]
    assert path.stat().st_size < 10_000
    assert gate.arming_status(path)[0] is False  # a seeded file never arms the real gate


# ------------------------------------------------------------------ unarmed path (exit 0, loud notice, nothing runs)

def run_cli(*argv):
    return cli_main(["gate", *argv])


def test_unarmed_no_baseline_exits_zero_with_notice_and_never_touches_db_or_cache(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    rc = run_cli("--baseline", str(tmp_path / "missing.json"), "--cache", str(tmp_path / "missing.jsonl.gz"),
                 "--database-url", "postgresql://nobody@127.0.0.1:1/never_connected_test")
    out = capsys.readouterr().out
    assert rc == 0
    assert "GATE NOT ARMED" in out and "::warning title=GATE NOT ARMED::" in out
    assert "no baseline file" in out and "release-rules.md" in out


def test_unarmed_when_only_a_seeded_baseline_exists(capsys):
    rc = run_cli("--baseline", str(seed.SEEDED_BASELINE), "--cache", "nonexistent.jsonl.gz")
    out = capsys.readouterr().out
    assert rc == 0 and "GATE NOT ARMED" in out and "seeded/fake fixture" in out


def test_repo_default_state_is_unarmed_today(capsys, monkeypatch):
    """The repo as committed has no real baseline, so CI must not fail for lack of one."""
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo)
    armed, _ = gate.arming_status(Path(gate.DEFAULT_BASELINE))
    if armed:
        pytest.skip("a real baseline has been committed: the gate is armed (this test describes the pre-arming state)")
    assert run_cli() == 0
    assert "GATE NOT ARMED" in capsys.readouterr().out


def test_status_reports_armed_state_and_github_output(tmp_path, capsys, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert run_cli("--status", "--baseline", str(tmp_path / "none.json")) == 0
    assert run_cli("--status", "--baseline", str(write(tmp_path / "r.json", doc(R, R)))) == 0
    assert out.read_text().splitlines() == ["armed=false", "armed=true"]


def test_test_split_and_noncompliant_invocations_refused(capsys, tmp_path):
    assert run_cli("--split", "test") == 2
    assert run_cli("--refresh-cache", "--write-baseline") == 2
    # armed, but a non-test database name is refused before anything is created
    b = write(tmp_path / "b.json", doc(R, R))
    assert run_cli("--baseline", str(b), "--database-url", "postgresql://u@127.0.0.1:1/scribe") == 1
    assert "REFUSING" in capsys.readouterr().err


def test_refresh_is_refused_in_ci_without_calling_anything(monkeypatch, capsys):
    monkeypatch.setenv("CI", "true")
    assert run_cli("--refresh-cache", "--database-url", "postgresql://u@127.0.0.1:1/x_test") == 1
    assert "refused in CI" in capsys.readouterr().err


def test_cache_file_roundtrip_is_sorted_pruned_and_stable(tmp_path):
    c = gate.TrackingCache(tmp_path / "w.sqlite", "record")
    c._put("b", [1]); c._put("a", [2]); c._put("unused", [3])
    c.used.discard("unused")
    p1, p2 = tmp_path / "1.gz", tmp_path / "2.gz"
    assert gate.export_cache_file(c, p1) == 2 and gate.export_cache_file(c, p2) == 2
    assert p1.read_bytes() == p2.read_bytes()
    keys = [json.loads(l)["key"] for l in gzip.open(p1, "rt")]
    assert keys == ["a", "b"]
    c2 = gate.TrackingCache(tmp_path / "w2.sqlite", "replay")
    assert gate.load_cache_file(p1, c2) == 2 and c2.used == set()  # loading does not count as use
    c.close(); c2.close()


# ------------------------------------------------------------------ end to end on the seeded fixture (DB)

def gate_workspaces() -> int:
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        return c.execute("select count(*) from workspaces where owner_user_id like 'gate-%'").fetchone()[0]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    if not DATABASE_URL_TEST:
        pytest.skip("DATABASE_URL_TEST not set")
    work = tmp_path_factory.mktemp("gate-seeded")
    paths = seed.record_cache(work, DATABASE_URL_TEST)
    paths["work"] = work
    paths["real_baseline"] = seed.SEEDED_BASELINE
    return paths


def check(env, *extra, baseline=None, cache=None, tau="0.5"):
    return run_cli("--dataset", str(env["dataset"]), "--corpus", str(env["corpus"]), "--cache", str(cache or env["cache"]),
                   "--baseline", str(baseline or env["real_baseline"]), "--database-url", DATABASE_URL_TEST,
                   "--tau", tau, "--accept-seeded-baseline", *extra)


@needs_db
def test_unchanged_run_passes_against_committed_seeded_baseline(env, capsys):
    """Also proves the committed seeded baseline is exactly reproducible from a fresh seeded cache."""
    before = gate_workspaces()
    rc = check(env, baseline=seed.SEEDED_BASELINE)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "GATE PASSED" in out and "FAIL" not in out.replace("GATE FAILED", "")
    assert gate_workspaces() == before  # throwaway workspaces cleaned up


@needs_db
def test_armed_path_runs_and_passes_without_any_flag_and_ignores_provider_keys(env, capsys, monkeypatch):
    for k in gate.PROVIDER_ENV:
        monkeypatch.setenv(k, "sk-not-a-real-key")
    rc = check(env)
    out = capsys.readouterr().out
    assert rc == 0 and "GATE PASSED" in out and "ignoring provider key" in out
    assert not any(k in os.environ for k in gate.PROVIDER_ENV)


@needs_db
def test_write_out_result_file(env, tmp_path, capsys):
    assert check(env, "--out", str(tmp_path), "--seeded") == 0
    res = json.loads((tmp_path / "gate-result.json").read_text())
    assert res["summary"]["answerable"] == 14 and res["cross_workspace_hits"] == 0 and res["stamp"] == seed.SEEDED


def rewrite_cache(src: Path, dst: Path, drop) -> int:
    rows = [json.loads(l) for l in gzip.open(src, "rt")]
    kept = [r for r in rows if not drop(r["key"])]
    with gzip.open(dst, "wt") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in kept))
    return len(rows) - len(kept)


@needs_db
@pytest.mark.parametrize("prefix,what", [("rr:", "rerank"), ("emb:", "embedding")])
def test_cache_miss_fails_with_refresh_message_and_never_calls_a_provider(env, tmp_path, capsys, prefix, what):
    cache = tmp_path / "holes.jsonl.gz"
    # drop exactly one entry of the kind under test
    rows = [json.loads(l) for l in gzip.open(env["cache"], "rt")]
    victim = next(r["key"] for r in rows if r["key"].startswith(prefix))
    assert rewrite_cache(env["cache"], cache, lambda k: k == victim) == 1
    before = gate_workspaces()
    rc = check(env, cache=cache)
    err = capsys.readouterr().err
    assert rc == 1
    assert "CACHE MISS" in err and "--refresh-cache" in err and "Refresh the cache locally" in err
    assert gate_workspaces() == before


@needs_db
def test_missing_cache_file_fails_when_armed(env, tmp_path, capsys):
    assert check(env, cache=tmp_path / "absent.jsonl.gz") == 1
    assert "does not exist" in capsys.readouterr().err


@needs_db
def test_changed_question_is_a_cache_miss(env, tmp_path, capsys):
    """A PR that edits the dataset questions needs fresh query embeddings: it must fail, not call a provider."""
    ds = tmp_path / "ds"
    ds.mkdir()
    rows = [json.loads(l) for l in (env["dataset"] / "cases.jsonl").read_text().splitlines()]
    rows[0]["question"] += " extra"
    (ds / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    rc = run_cli("--dataset", str(ds), "--corpus", str(env["corpus"]), "--cache", str(env["cache"]),
                 "--baseline", str(env["real_baseline"]), "--database-url", DATABASE_URL_TEST, "--tau", "0.5",
                 "--accept-seeded-baseline")
    assert rc == 1 and "CACHE MISS" in capsys.readouterr().err


# --- deliberately broken retrieval must fail the gate -------------------------------------------------------------

def reversed_rrf(real):
    def broken(lists, k=60):
        out = list(reversed(real(lists, k)))
        for i, r in enumerate(out, 1):
            r.fusion_rank = i
        return out
    return broken


@pytest.fixture()
def c_baseline(env):
    """Baseline for the no-rerank hybrid profile (hybrid-rrf), written from the same seeded cache."""
    out = env["work"] / "hybrid-rrf-baseline.json"
    if not out.exists():
        seed.write_baseline(env["work"], env, DATABASE_URL_TEST, out, profile="hybrid-rrf")
    return out


@needs_db
def test_hybrid_rrf_unchanged_passes_and_reversed_rrf_fails_the_gate(env, c_baseline, capsys, monkeypatch):
    from scribe_intel.retrieval import pipeline

    assert check(env, "--profile", "hybrid-rrf", baseline=c_baseline) == 0
    assert "GATE PASSED" in capsys.readouterr().out
    monkeypatch.setattr(pipeline, "rrf_fuse", reversed_rrf(pipeline.rrf_fuse))
    rc = check(env, "--profile", "hybrid-rrf", baseline=c_baseline)
    out = capsys.readouterr().out
    assert rc == 1 and "GATE FAILED" in out
    fails = [l for l in out.splitlines() if l.startswith("R1") and "FAIL" in l]
    assert len(fails) == 2 and "upper bound < 0" in fails[0]  # recall@5 and mrr both regress beyond the interval


@needs_db
def test_reversed_rrf_with_rerank_profile_also_fails(env, capsys, monkeypatch):
    """With the reranker in the path a broken fusion changes the rerank input, so replay cannot answer: still a FAIL."""
    from scribe_intel.retrieval import pipeline

    monkeypatch.setattr(pipeline, "rrf_fuse", reversed_rrf(pipeline.rrf_fuse))
    assert check(env) == 1
    assert "CACHE MISS" in capsys.readouterr().err


@needs_db
def test_cross_workspace_leak_fails_r2_end_to_end(env, c_baseline, capsys, monkeypatch):
    """Remove the workspace predicates from the retrievers' SQL: the decoy workspace's identical documents leak in."""
    before = gate_workspaces()
    from scribe_intel.retrieval import dense, lexical, queries

    leaky_joins = (queries.SCOPE_JOINS.replace(" and g.workspace_id = %(w)s", "")
                   .replace(" and ai.workspace_id = %(w)s", "").replace(" and f.workspace_id = %(w)s", ""))
    assert leaky_joins != queries.SCOPE_JOINS
    real_where = queries.scope_where
    for mod in (dense, lexical):
        monkeypatch.setattr(mod, "SCOPE_JOINS", leaky_joins)
        monkeypatch.setattr(mod, "scope_where", lambda *a, **k: real_where(*a, **k).replace("c.workspace_id = %(w)s", "true"))
    rc = check(env, "--profile", "hybrid-rrf", baseline=c_baseline)
    out = capsys.readouterr().out
    assert rc == 1
    line = next(l for l in out.splitlines() if l.startswith("R2"))
    assert "FAIL" in line and "belong to another workspace" in line and " 0 returned" not in line
    assert gate_workspaces() == before


@needs_db
async def test_count_cross_workspace_hits_detects_a_planted_foreign_chunk(env):
    from _db import aconnect

    conn = await aconnect(DATABASE_URL_TEST)
    try:
        wids = []
        for label in ("a", "b"):
            cur = await conn.execute("insert into workspaces(kind, owner_user_id) values ('eval', %s) returning id",
                                     (f"gate-unit-{label}-{os.getpid()}",))
            wids.append(str((await cur.fetchone())[0]))
        from scribe_ingest.embedding import FakeEmbedder
        from scribe_eval.ingest_corpus import ingest_corpus

        names = {"x.md": b"# X\n\nplain seeded text for the planted chunk check.\n"}
        for w in wids:
            res = await ingest_corpus(conn, w, ["x.md"], lambda n: names[n], FakeEmbedder())
            assert res[0][1].status == "ACTIVE"
        cur = await conn.execute("select id from chunks where workspace_id=%s", (wids[1],))
        foreign = [r[0] for r in await cur.fetchall()]
        cur = await conn.execute("select id from chunks where workspace_id=%s", (wids[0],))
        own = [r[0] for r in await cur.fetchall()]
        assert own and foreign
        assert await gate.count_cross_workspace_hits(conn, wids[0], own) == 0
        assert await gate.count_cross_workspace_hits(conn, wids[0], own + foreign) == len(foreign)
        assert await gate.count_cross_workspace_hits(conn, wids[0], []) == 0
    finally:
        gate.purge_workspaces(DATABASE_URL_TEST, wids)
        await conn.close()


def test_unreviewed_baseline_creation_refused_before_replay(tmp_path, capsys):
    dataset = seed.build_dataset(tmp_path / "dataset")
    baseline = tmp_path / "baseline.json"
    assert run_cli("--write-baseline", "--dataset", str(dataset), "--baseline", str(baseline)) == 1
    assert "not human-reviewed" in capsys.readouterr().err
    assert not baseline.exists()


def test_unreviewed_or_fake_measurement_cannot_arm(tmp_path):
    for override in ({"dataset_reviewed": False}, {"models": {"embedding": "fake", "rerank": None}}):
        with pytest.raises(gate.GateError):
            gate.arming_status(write(tmp_path / "baseline.json", doc(R, R, **override)))


def test_missing_configuration_cannot_compare():
    baseline = doc(R, R)
    del baseline["profile_config_hash"]
    assert not gate.compare(baseline, doc(R, R), n_resamples=100).passed


@pytest.mark.parametrize("field,value", [("measurement", None), ("measurement", False), ("seeded", None)])
def test_incomplete_measurement_metadata_fails_arming(tmp_path, field, value):
    baseline = doc(R, R)
    if value is None:
        del baseline[field]
    else:
        baseline[field] = value
    with pytest.raises(gate.GateError):
        gate.arming_status(write(tmp_path / "invalid.json", baseline))


@pytest.mark.parametrize("side", ["baseline", "current"])
@pytest.mark.parametrize("mutation", ["missing_metric", "nan_metric", "missing_hits", "bad_hits", "missing_missed", "bad_missed", "infinite_tau"])
def test_malformed_results_fail_closed(side, mutation):
    baseline, current = doc(R, R), doc(R, R)
    target = baseline if side == "baseline" else current
    if mutation == "missing_metric":
        del target["cases"]["c0"]["metrics"]["mrr"]
    elif mutation == "nan_metric":
        target["cases"]["c0"]["metrics"]["mrr"] = float("nan")
    elif mutation == "missing_hits":
        del target["cross_workspace_hits"]
    elif mutation == "bad_hits":
        target["cross_workspace_hits"] = True
    elif mutation == "missing_missed":
        del target["cases"]["u0"]["missed_abstention"]
    elif mutation == "bad_missed":
        target["cases"]["u0"]["missed_abstention"] = 0
    else:
        target["tau"] = float("inf")
    assert not gate.compare(baseline, current, n_resamples=100).passed


def test_answerability_change_cannot_hide_regressing_case():
    baseline, current = doc(R, R), doc(R, R)
    current["cases"]["c0"] = {"answerable": False, "missed_abstention": False}
    assert not gate.compare(baseline, current, n_resamples=100).passed

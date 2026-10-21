"""`scribe-eval run-profile`: run retrieval profiles against the ALREADY INGESTED eval workspace of a dataset and write
rankings JSONL for `scribe-eval score` (M1 / T17 groundwork, A4a).

* Never reads the environment for a database URL.
* Rerank profiles need VOYAGE_API_KEY; without it they are REFUSED here instead of silently degrading to the fused
  order (which would mislabel a no-rerank result as a rerank result). Profiles are looked up by name in
  ``scribe_intel.retrieval.profiles.BY_NAME``; none is hard-coded here.
* Query embeddings use the same disk cache as `ingest-corpus`.

Datasets and corpora (A4a)
--------------------------
``--dataset <dir>`` (default golden-v1) selects the cases AND the eval workspace: the workspace owned by
``ingest_corpus.eval_owner(dataset)`` (``eval-<dataset dir name>``). ``--corpus <dir>`` (default fixtures/corpus) is
recorded in each rankings row's ``meta`` (the corpus must already have been ingested with the same ``--dataset``).

Several profiles: ``--profiles A,B,C`` runs them one after another; ``--out`` is then a DIRECTORY and each profile
writes ``<out>/<profile>.jsonl`` (``--profile X`` keeps ``--out`` as a single file).

Rankings rows (additive to the pre-A4a format)
----------------------------------------------
Every ranked item also carries provenance, looked up from the database after retrieval: ``workspace_id``,
``source_id`` and ``source_deleted`` (so `score` can compute ``leak_rate``). Every record also carries ``embed_ms`` (the
query embedding, measured as its own call BEFORE retrieval, REAL provider time unless ``embed_cached``),
``retrieval_ms`` (the handler with the embedding already in hand) and ``latency_ms`` = embed_ms + retrieval_ms, plus
``cost`` (embed + rerank usage priced by scribe_eval.cost; cached calls are $0) and ``meta.workspace_id`` /
``workspace_owner`` / ``dataset`` / ``corpus`` / ``milestone_id``. A sidecar ``<rankings>.meta.json`` records the
per-profile latency p50/p95 and the cost totals.

Test split (INV-12): ``--split test`` is refused unless ``--milestone-report`` AND ``--milestone-id <id>`` are given.
It then refuses unreviewed cases (``scribe_eval.review.require``), refuses ``--limit`` (a partial run would burn the
once-only lock) and takes the dataset's lock ``<runs-dir>/<dataset>/test-split.lock`` (default
``benchmarks/runs/<dataset>/test-split.lock``, git-ignored). The lock records, per profile, the milestone id, status,
commit and timestamps, and, per dataset, the cases.jsonl sha256. Rules:

* the first test-split run of a (dataset, profile) is allowed and written to the lock as ``started`` BEFORE it runs;
* a second run of the same (dataset, profile) is REFUSED unless it carries the same ``--milestone-id`` (resuming an
  interrupted milestone; profiles already ``completed`` in that milestone are skipped, not re-run);
* a milestone id's profile set is fixed when it is first used; a changed dataset sha refuses everything.
Spend checks happen before the lock is taken, so a refusal never burns it.

Spend (scribe_eval.cost): a pre-run estimate (cases x per-case embed + rerank estimate; zero when the cache is in
replay mode) is checked against the ledger and the $10 cap (``--spend-cap`` can only lower it) before any provider call,
exit 2 on refusal. The actual spend is appended to ``benchmarks/spend-ledger.jsonl`` afterwards.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

from . import cost as costmod
from .dataset import load_cases
from .ingest_corpus import DEFAULT_CORPUS, DEFAULT_DATASET, EVAL_OWNER, dataset_name, eval_owner  # noqa: F401
from .metrics import latency_summary

RANKING_DEPTH = 20
SPLITS = ("dev", "validation")
DEFAULT_RUNS_DIR = "benchmarks/runs"
RERANK_DOC_TOKENS_EST = 400  # per candidate sent to the reranker (estimate used only for the pre-run budget)


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


def dataset_sha256(dataset_dir: Path) -> str:
    return hashlib.sha256((dataset_dir / "cases.jsonl").read_bytes()).hexdigest()


def profiles_from_args(args) -> list[str]:
    one, many = getattr(args, "profile", None), getattr(args, "profiles", None)
    if one and many:
        raise RunProfileError("pass --profile OR --profiles, not both")
    names = [p.strip() for p in many.split(",")] if many else ([one] if one else [])
    if not names or any(not n for n in names):
        raise RunProfileError("pass --profile NAME or --profiles A,B,...")
    if len(set(names)) != len(names):
        raise RunProfileError("--profiles lists a profile twice")
    return names


def out_paths(args, names: list[str]) -> dict[str, Path]:
    out = Path(args.out)
    if getattr(args, "profiles", None):
        return {n: out / f"{n}.jsonl" for n in names}
    return {names[0]: out}


# ---------------------------------------------------------------- test-split lock (INV-12)

def lock_path(runs_dir: str | Path, dataset: str) -> Path:
    return Path(runs_dir) / dataset / "test-split.lock"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_lock(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def acquire_test_lock(runs_dir: str | Path, dataset: str, sha: str, profiles: list[str], milestone_id: str,
                      commit: str) -> list[str]:
    """Record ``profiles`` as started under ``milestone_id`` and return those still to run (completed ones are
    skipped). Raises RunProfileError when INV-12's once-only rule forbids it."""
    path = lock_path(runs_dir, dataset)
    lock: dict[str, Any] = (json.loads(path.read_text(encoding="utf-8")) if path.is_file()
                            else {"dataset": dataset, "dataset_sha256": sha, "entries": {}, "milestones": {}})
    if lock["dataset_sha256"] != sha:
        raise RunProfileError(f"{path}: cases.jsonl changed since the test split of {dataset!r} was locked "
                              f"(was {lock['dataset_sha256'][:12]}, now {sha[:12]}); refusing")
    ms = lock["milestones"].get(milestone_id)
    if ms is not None and sorted(ms["profiles"]) != sorted(profiles):
        raise RunProfileError(f"milestone {milestone_id!r} was started with profiles {ms['profiles']}; its profile set "
                              "cannot change (resume with the same --profiles, or use a new milestone id for new profiles)")
    for p in profiles:
        e = lock["entries"].get(p)
        if e and e["milestone_id"] != milestone_id:
            raise RunProfileError(
                f"INV-12: the test split of {dataset!r} was already run for profile {p!r} (milestone "
                f"{e['milestone_id']!r}, {e['started_at']}); it is scored once per dataset. Only resuming that "
                f"milestone with --milestone-id {e['milestone_id']} is allowed.")
    todo = []
    for p in profiles:
        e = lock["entries"].get(p)
        if e and e["status"] == "completed":
            continue
        todo.append(p)
        lock["entries"].setdefault(p, {"milestone_id": milestone_id, "status": "started", "commit": commit,
                                       "started_at": _now(), "completed_at": None})
    lock["milestones"].setdefault(milestone_id, {"profiles": sorted(profiles), "commit": commit, "created_at": _now()})
    _write_lock(path, lock)
    return todo


def mark_profile_complete(runs_dir: str | Path, dataset: str, profile: str, out: Path) -> None:
    path = lock_path(runs_dir, dataset)
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock["entries"][profile].update({"status": "completed", "completed_at": _now(), "out": str(out)})
    _write_lock(path, lock)


# ---------------------------------------------------------------- embedding / spend helpers

def build_embedder(cache):
    """Real embedder over the disk cache (tests monkeypatch this to inject a fake)."""
    from scribe_ingest.embedding import GeminiEmbedder

    from .embed_cache import DiskEmbeddingCache

    return GeminiEmbedder(cache=DiskEmbeddingCache(cache))


class _PrecomputedEmbedder:
    """Returns a query vector that was already embedded (and timed) outside the retrieval handler."""

    is_fake = False
    model = "precomputed"

    def __init__(self, vec: list[float]) -> None:
        self.vec, self.dimensions = vec, len(vec)

    async def embed_queries(self, texts):
        return [self.vec for _ in texts]


def estimate_run_usd(profile_names: list[str], questions: list[str], embed_model: str, replay: bool) -> float:
    """Pre-run estimate: for each profile x case, the query embedding (if the profile is dense) plus, for reranking
    profiles, ``top_n`` documents of RERANK_DOC_TOKENS_EST tokens. Replay mode never calls a provider: $0."""
    from scribe_intel.retrieval.profiles import BY_NAME

    if replay:
        return 0.0
    usd = 0.0
    for name in profile_names:
        cfg = BY_NAME[name].config
        for q in questions:
            qt = costmod.estimate_tokens(q)
            if cfg.dense_top_k is not None:
                usd += costmod.tokens_usd(embed_model, qt)
            if cfg.rerank:
                usd += costmod.tokens_usd(cfg.rerank_model or "rerank-2.5",
                                          qt * cfg.rerank_top_n + cfg.rerank_top_n * RERANK_DOC_TOKENS_EST)
    return usd


async def lookup_provenance(conn: psycopg.AsyncConnection, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    """{chunk_id: {workspace_id, source_id, source_deleted}} read on the privileged eval connection (no RLS context,
    so a chunk of ANOTHER workspace is still seen, which is exactly what the decoy check needs)."""
    if not chunk_ids:
        return {}
    cur = await conn.execute(
        "select c.id::text, c.workspace_id::text, f.id, (f.deleted_at is not null) from chunks c "
        "left join source_versions v on v.id = c.source_version_id left join files f on f.id = v.source_id "
        "where c.id = any(%s::uuid[])", (chunk_ids,))
    return {cid: {"workspace_id": ws, "source_id": sid, "source_deleted": bool(deleted) if sid is not None else None}
            for cid, ws, sid, deleted in await cur.fetchall()}


# ---------------------------------------------------------------- entry points

def run(args) -> int:
    split = args.split
    milestone = bool(getattr(args, "milestone_report", False))
    if split not in SPLITS and not (split == "test" and milestone):
        print("ERROR: the test split is milestone-only (INV-12): pass --milestone-report and --milestone-id to run it.",
              file=sys.stderr)
        return 2
    if split == "test" and not getattr(args, "milestone_id", None):
        print("ERROR: --split test needs --milestone-id (INV-12: it is how an interrupted milestone is resumed).",
              file=sys.stderr)
        return 2
    if split == "test" and getattr(args, "limit", 0):
        print("ERROR: --limit is refused on the test split (a partial run would burn the once-only lock).",
              file=sys.stderr)
        return 2
    if not args.database_url:
        print("ERROR: --database-url is required (the environment is never consulted).", file=sys.stderr)
        return 2
    if "test" not in args.database_url.rsplit("/", 1)[-1].split("?")[0]:
        print("ERROR: refusing a database whose name does not contain 'test'.", file=sys.stderr)
        return 2
    try:
        names = profiles_from_args(args)
        for n in names:
            check_profile(n, os.environ)
        return asyncio.run(_go(args, names))
    except (RunProfileError, costmod.SpendError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


async def _go(args, names: list[str]) -> int:
    from scribe_intel.retrieval.profiles import BY_NAME
    from scribe_intel.retrieval.registry import RetrieveContext, get_handler
    from scribe_intel.retrieval.rerank import CachedReranker, VoyageReranker

    from .cache import DiskCache
    from .runs import git_commit

    dataset_dir = Path(getattr(args, "dataset", None) or DEFAULT_DATASET)
    corpus = getattr(args, "corpus", None) or DEFAULT_CORPUS
    ds_name, owner = dataset_name(dataset_dir), eval_owner(dataset_dir)
    milestone_id = getattr(args, "milestone_id", None) if args.split == "test" else None
    runs_dir = getattr(args, "runs_dir", None) or DEFAULT_RUNS_DIR
    ledger = getattr(args, "ledger", None)
    outs = out_paths(args, names)

    all_cases = load_cases(dataset_dir / "cases.jsonl")
    cases = [c for c in all_cases if c.split == args.split]
    if args.split == "test":
        from .review.require import refusal_message, unreviewed_ids

        bad = unreviewed_ids(cases)
        if bad:
            print(refusal_message(bad, len(cases), f"run a milestone report on split '{args.split}'"), file=sys.stderr)
            return 2
    if getattr(args, "limit", 0):
        cases = cases[: args.limit]
    if not cases:
        raise RunProfileError(f"no cases in split '{args.split}' of {dataset_dir}")

    cache = DiskCache(args.cache_path)
    metered = costmod.MeteredEmbedder(build_embedder(cache))
    voyage_key = os.environ.get("VOYAGE_API_KEY", "")

    # spend pre-flight, BEFORE the lock and before any provider call
    estimate = estimate_run_usd(names, [c.question for c in cases], metered.model, cache.mode == "replay")
    total, cap = costmod.preflight(estimate, spend_cap=getattr(args, "spend_cap", None), path=ledger,
                                   what=f"run {','.join(names)} on {len(cases)} case(s) of {ds_name}/{args.split}")
    print(f"spend: pre-run estimate ${estimate:.4f}, ledger ${total:.4f}, cap ${cap:.2f}")

    async with await psycopg.AsyncConnection.connect(args.database_url, autocommit=True, prepare_threshold=None) as conn:
        row = await (await conn.execute("select id, kind from workspaces where owner_user_id = %s", (owner,))).fetchone()
        if not row or row[1] != "eval":
            raise RunProfileError(f"no eval workspace for dataset {ds_name!r} (owner {owner}); run `scribe-eval "
                                  f"ingest-corpus --dataset {dataset_dir}` first")
        wid = str(row[0])
        todo = names
        if args.split == "test":  # after every refusal that must not burn the lock (review, spend, workspace)
            todo = acquire_test_lock(runs_dir, ds_name, dataset_sha256(dataset_dir), names, milestone_id, git_commit())
            for n in names:
                if n not in todo:
                    print(f"profile {n}: already completed in milestone {milestone_id}; skipped")
        pool = AsyncConnectionPool(args.database_url, min_size=0, max_size=3, open=False,
                                   kwargs={"prepare_threshold": None, "autocommit": True})
        await pool.open()
        spend = costmod.RunSpend("run-profile", str(uuid.uuid4()))
        try:
            for name in todo:
                profile = BY_NAME[name]
                handler = get_handler(name)
                dense = profile.config.dense_top_k is not None
                out_path = outs[name]
                out_path.parent.mkdir(parents=True, exist_ok=True)
                rows: list[dict[str, Any]] = []
                degraded = 0
                with out_path.open("w", encoding="utf-8") as fh:
                    for case in cases:
                        meter = costmod.RerankMeter()

                        def reranker_for(model: str, meter=meter):
                            if not voyage_key:
                                return None
                            return costmod.MeteredReranker(
                                CachedReranker(VoyageReranker(api_key=voyage_key, model=model), cache), cache, meter)

                        embed_ms, embed_cached, embed_usage = 0.0, True, None
                        embedder = metered
                        if dense:  # embed as its own timed call so the latency includes the REAL embedding time
                            before = metered.live_texts
                            vec = (await metered.embed_queries([case.question]))[0]
                            embed_ms, embed_cached = metered.last_ms, metered.live_texts == before
                            embed_usage = costmod.Usage(metered.model, costmod.estimate_tokens(case.question), 0, True,
                                                        cached=embed_cached)
                            embedder = _PrecomputedEmbedder(vec)
                        ctx = RetrieveContext(pool=pool, workspace_id=wid, query=case.question, source_ids=None,
                                              limit=RANKING_DEPTH, embedder_for=lambda dims, e=embedder: e,
                                              reranker_for=reranker_for)
                        t0 = time.monotonic()
                        res = await handler(ctx)
                        retrieval_ms = (time.monotonic() - t0) * 1000
                        degraded += 1 if res.degraded else 0
                        ranked = res.ranked[:RANKING_DEPTH]
                        prov = await lookup_provenance(conn, [str(r.chunk_id) for r in ranked])
                        qcost = costmod.price_per_query(embed=embed_usage, rerank=costmod.rerank_usage(meter))
                        for u in (embed_usage, costmod.rerank_usage(meter)):
                            spend.add(u)

                        def score(r):
                            # native score of the final ordering: rerank, else RRF only when two lists were fused,
                            # else the single retriever's own score. Informational; scales differ across profiles.
                            if r.rerank_score is not None:
                                return r.rerank_score
                            if r.dense_rank is not None and r.lexical_rank is not None:
                                return r.fusion_score
                            return r.dense_score if r.dense_rank is not None else r.lexical_score

                        items = []
                        for r in ranked:
                            p = prov.get(str(r.chunk_id), {})
                            items.append({
                                "chunk_id": str(r.chunk_id), "doc": r.candidate.doc_name, "text": r.candidate.content,
                                "score": score(r), "method": "hybrid",
                                "workspace_id": p.get("workspace_id"),
                                "source_id": p.get("source_id", r.candidate.source_id),
                                "source_deleted": p.get("source_deleted"),
                            })
                        rec = {
                            "case_id": case.id, "ranking": items,
                            "latency_ms": round(embed_ms + retrieval_ms, 1), "embed_ms": round(embed_ms, 1),
                            "retrieval_ms": round(retrieval_ms, 1), "embed_cached": embed_cached if dense else None,
                            "cost": {"embedding_cache_stats": dict(cache.stats), **qcost.as_json()},
                            "degraded": list(res.degraded),
                            "meta": {"profile": name, "config_hash": profile.config_hash, "stage_ms": res.stage_ms,
                                     "workspace_id": wid, "workspace_owner": owner, "dataset": ds_name,
                                     "corpus": str(corpus), "split": args.split, "milestone_id": milestone_id},
                        }
                        rows.append(rec)
                        fh.write(json.dumps(rec) + "\n")
                lat = latency_summary(rows)
                out_path.with_name(out_path.name + ".meta.json").write_text(json.dumps({
                    "profile": name, "dataset": ds_name, "split": args.split, "milestone_id": milestone_id,
                    "workspace_id": wid, "commit": git_commit(), "latency": lat,
                    "cost": costmod.cost_summary(r["cost"] for r in rows)}, indent=2), encoding="utf-8")
                if args.split == "test":
                    mark_profile_complete(runs_dir, ds_name, name, out_path)
                print(f"profile {name} split {args.split}: {len(cases)} cases, degraded {degraded}, latency p50 "
                      f"{lat['total_p50_ms']:.0f} ms p95 {lat['total_p95_ms']:.0f} ms (embed p50 "
                      f"{lat['embed_p50_ms'] or 0:.0f} ms, live embeds {lat['embed_live_n']}/{lat['embed_timed_n']}), "
                      f"wrote {out_path}")
        finally:
            await pool.close()
            if spend.usages and not all(costmod.is_fake_model(u.model_id) for u in spend.usages):
                rec = spend.write(ledger)
                print(f"spend ledger: +${rec['usd']:.4f} (estimated={rec['estimated']}, cached={rec['cached']}); "
                      f"total ${costmod.spend_total(ledger):.4f}")
    return 0

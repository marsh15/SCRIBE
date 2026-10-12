"""T31 retrieval regression gate: block pull requests that make retrieval worse.

``scribe-eval gate`` scores a golden-set split by REPLAYING cached provider responses (DESIGN D18):

  1. The corpus is indexed into two throwaway workspaces (the eval workspace and a DECOY holding identical
     documents) with the real indexer. Document embeddings come from the response cache.
  2. Every case of the split is run through the real retrieval strategy (``/retrieve`` handler: snapshot, dense +
     lexical, RRF, rerank, dedup). Query embeddings and rerank responses come from the cache.
  3. The rankings are scored with the normal metrics and compared with a committed baseline.

A cache miss is a FAILURE, never a provider call: this module builds its embedder/reranker so that no network
request can be made in replay mode and removes provider keys from the process environment first. Refreshing the
cache needs keys and is a separate, explicit, local-only command (``--refresh-cache``).

Release rules (also written in docs/scribe2/release-rules.md). Let ``delta = current - baseline`` per case.
  R1  For BOTH Recall@5 and MRR, the 95% paired-bootstrap interval of mean(delta) must not lie entirely below 0.
      FAIL iff the upper bound of that interval is < 0. (10,000 resamples of case ids, fixed seed, same code as
      ``scribe-eval compare``.) A mean drop whose interval still contains 0 passes with a WARNING.
  R2  Cross-workspace hits must be 0 (absolute, not relative to the baseline).
  R3  Missed abstentions must not exceed the baseline's. A missed abstention is an unanswerable case for which the
      gate would NOT abstain (top rerank score >= tau; no score at all counts as abstaining).
  P   Preconditions (refresh the baseline in the same PR if they trip legitimately): same split, dataset sha256,
      profile and case ids as the baseline, and no document that fails to index which the baseline indexed.

"GATE NOT ARMED": until a REAL baseline (measured, not seeded, not fake) is committed, the check exits 0 with a
loud notice and does nothing else.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg

from .bootstrap import N_RESAMPLES, paired_bootstrap
from .cache import CacheMiss, DiskCache
from .dataset import Case, load_cases
from .embed_cache import DiskEmbeddingCache
from .metrics import CaseResult, evaluate_case
from .runs import git_commit
from .scoring import dataset_version

FORMAT = 1
DEFAULT_PROFILE = "hybrid-rrf-rerank"
DEFAULT_SPLIT = "validation"
DEFAULT_DATASET = "benchmarks/datasets/golden-v1"
DEFAULT_CORPUS = "fixtures/corpus"
DEFAULT_BASELINE = "benchmarks/gate/baseline.json"
DEFAULT_CACHE = "benchmarks/gate/cache.jsonl.gz"
GATED_METRICS: tuple[str, ...] = ("recall@5", "mrr")
STORED_METRICS: tuple[str, ...] = ("recall@5", "mrr", "recall@10", "hit@5", "ndcg@10")
PROVIDER_ENV = ("GOOGLE_GENERATIVE_AI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "VOYAGE_API_KEY")
RANKING_DEPTH = 20
SEEDED_STAMP = "SEEDED FIXTURE - NOT A MEASUREMENT"
REFRESH_HELP = (
    "Refresh the cache locally with your provider keys:  GOOGLE_GENERATIVE_AI_API_KEY=... VOYAGE_API_KEY=... "
    "scribe-eval gate --refresh-cache --database-url <throwaway *test* db>  then commit benchmarks/gate/cache.jsonl.gz "
    "(see docs/scribe2/release-rules.md)."
)


class GateError(Exception):
    """A problem that makes the gate unable to give a verdict (reported as FAIL, exit 1)."""


class GateCacheMiss(GateError):
    pass


# ------------------------------------------------------------------ cache (jsonl.gz <-> sqlite working copy)

class TrackingCache(DiskCache):
    """DiskCache that remembers which keys a run used, so a refresh exports exactly what replay will ask for."""

    def __init__(self, path: str | Path, mode: str) -> None:
        super().__init__(path, mode=mode)
        self.used: set[str] = set()

    def _get(self, key: str):  # type: ignore[override]
        found, value = super()._get(key)
        if found:
            self.used.add(key)
        return found, value

    def _put(self, key: str, value: Any) -> None:  # type: ignore[override]
        self.used.add(key)
        super()._put(key, value)

    def rows(self, keys: set[str] | None = None) -> list[tuple[str, Any]]:
        out = []
        for key, value in self._db.execute("SELECT key, value FROM cache ORDER BY key"):
            if keys is None or key in keys:
                out.append((key, json.loads(value)))
        return out


class GateEmbeddingCache(DiskEmbeddingCache):
    """Rounds recorded vectors to 6 decimals: halves the committed cache; cosine order is unaffected."""

    def put(self, key: str, value: list[float]) -> None:
        super().put(key, [round(float(x), 6) for x in value])


def load_cache_file(path: Path, cache: DiskCache) -> int:
    """Import a ``.jsonl`` / ``.jsonl.gz`` cache file into the working sqlite cache."""
    opener = gzip.open if path.suffix == ".gz" else open
    n = 0
    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[operator]
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            DiskCache._put(cache, rec["key"], rec["value"])  # noqa: SLF001 - bypass usage tracking
            n += 1
    return n


def export_cache_file(cache: TrackingCache, path: Path) -> int:
    """Write the keys used in this run, sorted, with a fixed gzip header (stable bytes, reviewable diffs)."""
    rows = cache.rows(cache.used)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps({"key": k, "value": v}, separators=(",", ":")) + "\n" for k, v in rows).encode()
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(body)
    return len(rows)


# ------------------------------------------------------------------ providers (replay never reaches a network)

class _NoProviderReranker:
    """Inner reranker for replay: a cache hit never gets here; reaching it is a bug, so it fails loudly."""

    is_fake = False

    def __init__(self, model: str) -> None:
        self.model = model

    async def rerank(self, query, docs, top_n):  # noqa: ANN001
        raise GateCacheMiss("replay mode: a rerank provider call was attempted. " + REFRESH_HELP)


@dataclass
class Providers:
    """Factories so the same replay code serves replay (no network), refresh (live keys) and the seeded tests."""

    embedder: Callable[[Any], Any]          # (embedding cache adapter) -> Embedder
    reranker: Callable[[str], Any]          # (rerank model) -> inner Reranker (wrapped by CachedReranker)
    mode: str                               # "replay" | "record"


def replay_providers() -> Providers:
    from scribe_ingest.embedding import GeminiEmbedder
    import httpx

    class _Blocked(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):  # noqa: ANN001
            raise GateCacheMiss("replay mode: a network request was attempted. " + REFRESH_HELP)

    return Providers(
        embedder=lambda cache: GeminiEmbedder(api_key="", cache=cache, transport=_Blocked()),
        reranker=lambda model: _NoProviderReranker(model),
        mode="replay",
    )


def live_providers() -> Providers:
    from scribe_ingest.embedding import GeminiEmbedder
    from scribe_intel.retrieval.rerank import VoyageReranker

    g, v = os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY", ""), os.environ.get("VOYAGE_API_KEY", "")
    if not g or not v:
        raise GateError("--refresh-cache needs GOOGLE_GENERATIVE_AI_API_KEY and VOYAGE_API_KEY in the environment")
    return Providers(
        embedder=lambda cache: GeminiEmbedder(api_key=g, cache=cache),
        reranker=lambda model: VoyageReranker(api_key=v, model=model),
        mode="record",
    )


# ------------------------------------------------------------------ DB helpers

_PURGE_FIRST = ("source_version_ocr_pages", "retrieval_traces", "retrieval_candidates", "evidence_items",
                "evidence_bundles", "rag_traces", "claim_verifications", "chats", "chunk_source_spans",
                "chunk_embeddings_1536", "chunk_embeddings_3072", "chunks", "files")
_PURGE_LEFTOVERS = ("active_indexes", "document_blocks", "index_generations", "source_versions", "workspace_members")


def purge_workspaces(url: str, wids: list[str]) -> None:
    """Delete every row of the given (throwaway gate) workspaces, then the workspaces."""
    if not wids:
        return
    with psycopg.connect(url, autocommit=True) as c:
        for table in _PURGE_FIRST + _PURGE_LEFTOVERS:
            c.execute(f"delete from {table} where workspace_id = any(%s::uuid[])", (wids,))  # noqa: S608 - fixed names
        c.execute("delete from workspaces where id = any(%s::uuid[])", (wids,))


def require_test_database(url: str) -> None:
    from scribe_ingest.embedding import database_name

    name = database_name(url)
    if "test" not in name.lower():
        raise GateError(f"REFUSING to run the gate against database {name!r}: it creates and deletes throwaway "
                        "workspaces and must only run on a database whose name contains 'test'.")


async def count_cross_workspace_hits(conn: psycopg.AsyncConnection, workspace_id: str, chunk_ids: list[Any]) -> int:
    """Number of returned chunks that belong to a workspace other than ``workspace_id`` (the scoping contract of
    INV-2/INV-3 checked on the output side). Chunk ids that no longer exist are not hits."""
    if not chunk_ids:
        return 0
    cur = await conn.execute(
        "select count(*) from chunks where id = any(%s) and workspace_id <> %s", (chunk_ids, workspace_id))
    return int((await cur.fetchone())[0])


# ------------------------------------------------------------------ replay

@dataclass
class ReplayOutput:
    records: dict[str, dict[str, Any]]
    failed_docs: dict[str, str]
    cross_workspace_hits: int
    degraded_cases: int
    cache_stats: dict[str, int]
    embed_model: str
    rerank_model: str | None
    profile_config_hash: str
    tau: float
    tau_calibrated: bool


def _miss_message(cache: DiskCache, where: str, detail: str | None = None) -> str:
    return (f"CACHE MISS during {where} ({cache.misses} missing entr{'y' if cache.misses == 1 else 'ies'}): the "
            "cached provider responses do not cover this code or data. Any change to chunking, document text, the "
            "case questions, or what the retriever hands to the reranker (candidate pool, fusion order, document "
            "text) needs a refreshed cache. " + REFRESH_HELP + (f" [{detail}]" if detail else ""))


async def replay_retrieval(
    *, database_url: str, corpus_dir: Path, cases: list[Case], profile_name: str, cache: TrackingCache,
    providers: Providers, tau: float | None = None, handler_override: Any = None,
) -> ReplayOutput:
    from scribe_intel.retrieval.profiles import BY_NAME, gate_for
    from scribe_intel.retrieval.registry import RetrieveContext, get_handler
    from scribe_intel.retrieval.rerank import CachedReranker
    from psycopg_pool import AsyncConnectionPool

    from .ingest_corpus import ingest_corpus, list_corpus

    profile = BY_NAME.get(profile_name)
    handler = handler_override or get_handler(profile_name)
    if profile is None or handler is None:
        raise GateError(f"unknown hybrid retrieval profile {profile_name!r}; choose one of {sorted(BY_NAME)}")
    gate_cfg = gate_for(profile_name)
    effective_tau = gate_cfg.tau if tau is None else tau
    rerank_model = profile.config.rerank_model if profile.config.rerank else None

    embedder = providers.embedder(GateEmbeddingCache(cache))
    if embedder.dimensions != 1536:  # type: ignore[attr-defined]
        raise GateError("the gate only supports the 1536-dimension structural-v1 index")

    def reranker_for(model: str):
        return CachedReranker(providers.reranker(model), cache)

    names = list_corpus(corpus_dir)
    loader = lambda n: (corpus_dir / n).read_bytes()  # noqa: E731
    created: list[str] = []
    pool = None
    conn = await psycopg.AsyncConnection.connect(database_url, autocommit=True, prepare_threshold=None)
    try:
        wids: dict[str, str] = {}
        for label in ("eval", "decoy"):
            cur = await conn.execute("insert into workspaces(kind, owner_user_id) values ('eval', %s) returning id",
                                     (f"gate-{label}-{uuid.uuid4()}",))
            wids[label] = str((await cur.fetchone())[0])
            created.append(wids[label])
        failed: dict[str, str] = {}
        for label in ("eval", "decoy"):
            results = await ingest_corpus(conn, wids[label], names, loader, embedder, ocr=None)
            if providers.mode == "replay" and cache.misses:
                raise GateCacheMiss(_miss_message(cache, "document indexing"))
            for name, r in results:
                if r.status != "ACTIVE":
                    failed.setdefault(name, r.error_code or r.status)
        pool = AsyncConnectionPool(database_url, min_size=0, max_size=3, open=False,
                                   kwargs={"prepare_threshold": None, "autocommit": True})
        await pool.open()
        records: dict[str, dict[str, Any]] = {}
        leaks = degraded = 0
        for case in cases:
            ctx = RetrieveContext(pool=pool, workspace_id=wids["eval"], query=case.question, source_ids=None,
                                  limit=RANKING_DEPTH, embedder_for=lambda dims: embedder,
                                  reranker_for=reranker_for)
            try:
                res = await handler(ctx)
            except CacheMiss as exc:
                raise GateCacheMiss(_miss_message(cache, f"retrieval for case {case.id}", str(exc)[:160])) from None
            leaks += await count_cross_workspace_hits(conn, wids["eval"], [r.chunk_id for r in res.ranked])
            degraded += 1 if res.degraded else 0
            records[case.id] = {
                "case_id": case.id,
                "ranking": [
                    {"chunk_id": str(r.chunk_id), "doc": r.candidate.doc_name, "text": r.candidate.content,
                     "score": r.rerank_score, "method": "hybrid"}
                    for r in res.ranked[:RANKING_DEPTH]
                ],
                "degraded": bool(res.degraded),
            }
        if providers.mode == "replay" and cache.misses:
            raise GateCacheMiss(_miss_message(cache, "retrieval"))
        return ReplayOutput(
            records=records, failed_docs=failed, cross_workspace_hits=leaks, degraded_cases=degraded,
            cache_stats=cache.stats, embed_model=embedder.model, rerank_model=rerank_model,  # type: ignore[attr-defined]
            profile_config_hash=profile.config_hash, tau=effective_tau, tau_calibrated=gate_cfg.tau_calibrated)
    finally:
        if pool is not None:
            await pool.close()
        await conn.close()
        await asyncio.get_running_loop().run_in_executor(None, purge_workspaces, database_url, created)


# ------------------------------------------------------------------ result document

def build_result(
    *, out: ReplayOutput, cases: list[Case], dataset_dir: Path, split: str, profile: str, seeded: bool,
) -> dict[str, Any]:
    results: list[CaseResult] = [evaluate_case(c, out.records[c.id]) for c in cases]
    case_doc: dict[str, Any] = {}
    missed = 0
    for r in results:
        entry: dict[str, Any] = {"answerable": r.answerable}
        if r.answerable:
            entry["metrics"] = {k: round(r.metrics[k], 6) for k in STORED_METRICS}  # type: ignore[index]
        else:
            entry["top_score"] = None if r.top_score is None else round(r.top_score, 6)
            entry["missed_abstention"] = r.top_score is not None and r.top_score >= out.tau
            missed += 1 if entry["missed_abstention"] else 0
        case_doc[r.case_id] = entry
    ans = [r for r in results if r.answerable]
    models = {"embedding": out.embed_model, "rerank": out.rerank_model}
    fake = any("fake" in str(m).lower() for m in models.values() if m)
    meta_path = dataset_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    summary: dict[str, Any] = {"answerable": len(ans), "unanswerable": len(results) - len(ans),
                               "missed_abstentions": missed}
    for m in GATED_METRICS:
        summary[m] = round(sum(r.metrics[m] for r in ans) / len(ans), 6) if ans else None  # type: ignore[index]
    measurement = not seeded and not fake
    return {
        "format": FORMAT, "kind": "retrieval-gate-result", "split": split, "profile": profile,
        "profile_config_hash": out.profile_config_hash, "dataset_version": dataset_version(dataset_dir),
        "dataset_sha256": hashlib.sha256((dataset_dir / "cases.jsonl").read_bytes()).hexdigest(),
        "dataset_review_status": meta.get("review_status", "unknown"),
        "models": models, "code_commit": git_commit(), "created_at": datetime.now(timezone.utc).isoformat(),
        "tau": out.tau, "tau_calibrated": out.tau_calibrated,
        "seeded": seeded, "measurement": measurement, "stamp": SEEDED_STAMP if seeded else (None if measurement else "FAKE - NOT A MEASUREMENT"),
        "failed_docs": sorted(out.failed_docs), "failed_doc_codes": dict(sorted(out.failed_docs.items())),
        "cross_workspace_hits": out.cross_workspace_hits, "degraded_cases": out.degraded_cases,
        "summary": summary, "cases": case_doc,
    }


# ------------------------------------------------------------------ arming

def load_baseline(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateError(f"baseline {path} exists but cannot be read: {exc}") from None
    if doc.get("format") != FORMAT or doc.get("kind") != "retrieval-gate-result" or not isinstance(doc.get("cases"), dict):
        raise GateError(f"baseline {path} is not a format-{FORMAT} gate result file")
    return doc


def arming_status(path: Path, accept_seeded: bool = False) -> tuple[bool, str]:
    """(armed, reason). Armed iff a REAL baseline exists: measured, not seeded, not fake."""
    if not path.is_file():
        return False, f"no baseline file at {path}"
    doc = load_baseline(path)
    if (doc.get("seeded") or not doc.get("measurement")) and not accept_seeded:
        return False, f"{path} is a seeded/fake fixture, not a measurement ({doc.get('stamp')})"
    return True, "armed"


NOT_ARMED_TEXT = (
    "GATE NOT ARMED: the retrieval regression gate did NOT run and protects nothing yet. {reason}. To arm it, run "
    "locally with your keys: `scribe-eval gate --refresh-cache ...` then `scribe-eval gate --write-baseline ...`, "
    "review the dataset (benchmarks/datasets/golden-v1/REVIEW_FIRST.md), and commit benchmarks/gate/cache.jsonl.gz "
    "and benchmarks/gate/baseline.json. Steps: docs/scribe2/release-rules.md."
)


# ------------------------------------------------------------------ comparison (pure)

@dataclass
class Check:
    rule: str
    name: str
    level: str  # "pass" | "warn" | "fail"
    detail: str


@dataclass
class GateReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.level != "fail" for c in self.checks)

    def add(self, rule: str, name: str, level: str, detail: str) -> None:
        self.checks.append(Check(rule, name, level, detail))

    def format(self) -> str:
        lines = [f"{'rule':<5}{'check':<34}{'result':<7}detail"]
        for c in self.checks:
            lines.append(f"{c.rule:<5}{c.name:<34}{c.level.upper():<7}{c.detail}")
        lines.append("")
        lines.append("GATE " + ("PASSED" if self.passed else "FAILED"))
        return "\n".join(lines)


def _metric_map(doc: dict[str, Any], metric: str) -> dict[str, float]:
    return {cid: c["metrics"][metric] for cid, c in doc["cases"].items() if c.get("answerable") and c.get("metrics")}


def compare(baseline: dict[str, Any], current: dict[str, Any], *, n_resamples: int = N_RESAMPLES) -> GateReport:
    rep = GateReport()
    pre_ok = True
    for key, label in (("split", "split"), ("dataset_sha256", "dataset"), ("profile", "profile")):
        if baseline.get(key) != current.get(key):
            pre_ok = False
            rep.add("P", f"same {label} as baseline", "fail",
                    f"baseline {str(baseline.get(key))[:16]!r} vs current {str(current.get(key))[:16]!r}: refresh the "
                    "baseline in this PR (scribe-eval gate --write-baseline) and have the diff reviewed")
    if set(baseline["cases"]) != set(current["cases"]):
        pre_ok = False
        rep.add("P", "same case ids as baseline", "fail",
                f"{len(set(baseline['cases']) ^ set(current['cases']))} case id(s) differ: refresh the baseline")
    new_failed = sorted(set(current.get("failed_docs", [])) - set(baseline.get("failed_docs", [])))
    if new_failed:
        pre_ok = False
        rep.add("P", "no newly unindexable documents", "fail", "documents that indexed in the baseline now fail: "
                + ", ".join(new_failed))
    if pre_ok:
        rep.add("P", "baseline comparable", "pass", f"{len(current['cases'])} cases, dataset and profile match")

    for metric in GATED_METRICS:
        base, cur = _metric_map(baseline, metric), _metric_map(current, metric)
        res = paired_bootstrap(base, cur, n_resamples=n_resamples)
        name = f"{metric} not worse"
        if res.n == 0:
            rep.add("R1", name, "fail", "no answerable cases shared with the baseline")
            continue
        detail = (f"baseline {sum(base.values())/len(base):.4f} -> current {sum(cur.values())/len(cur):.4f}; "
                  f"delta {res.mean_diff:+.4f}, 95% paired-bootstrap CI [{res.lo:+.4f}, {res.hi:+.4f}], n={res.n}")
        if res.hi < 0:
            rep.add("R1", name, "fail", detail + " : upper bound < 0, a statistically evident regression")
        elif res.mean_diff < 0:
            rep.add("R1", name, "warn", detail + " : mean dropped but the interval contains 0 (within noise)")
        else:
            rep.add("R1", name, "pass", detail)

    hits = int(current.get("cross_workspace_hits", 0))
    rep.add("R2", "cross-workspace hits == 0", "pass" if hits == 0 else "fail",
            f"{hits} returned chunk(s) belong to another workspace")

    def missed(doc: dict[str, Any]) -> int:
        return sum(1 for c in doc["cases"].values() if not c.get("answerable") and c.get("missed_abstention"))

    mb, mc = missed(baseline), missed(current)
    note = ""
    if baseline.get("tau") != current.get("tau"):
        note = f" (tau changed {baseline.get('tau')} -> {current.get('tau')})"
    if not current.get("tau_calibrated"):
        note += " (tau is UNCALIBRATED, so this check is weak until T17 calibrates it)"
    rep.add("R3", "missed abstentions not increased", "pass" if mc <= mb else "fail",
            f"baseline {mb}, current {mc} of {current['summary']['unanswerable']} unanswerable{note}")
    if current.get("degraded_cases"):
        rep.add("INFO", "degraded retrievals", "warn", f"{current['degraded_cases']} case(s) ran degraded")
    return rep


# ------------------------------------------------------------------ driver

def _annotate(kind: str, title: str, message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{kind} title={title}::{message.replace(chr(10), ' ')}")


def _scrub_provider_env() -> list[str]:
    removed = [k for k in PROVIDER_ENV if os.environ.pop(k, None) is not None]
    return removed


def _load_split(dataset_dir: Path, split: str) -> list[Case]:
    cases = [c for c in load_cases(dataset_dir / "cases.jsonl") if c.split == split]
    if not cases:
        raise GateError(f"no cases in split '{split}' of {dataset_dir}")
    return cases


def run_gate(args: Any, *, providers: Providers | None = None) -> int:
    """Entry point of ``scribe-eval gate``. ``providers`` is injectable for tests; the CLI never passes it."""
    if args.split == "test":
        print("ERROR: the gate never scores the test split (INV-12).", file=sys.stderr)
        return 2
    baseline_path, cache_path, dataset_dir = Path(args.baseline), Path(args.cache), Path(args.dataset)
    refresh = bool(args.refresh_cache)
    write_baseline = bool(args.write_baseline)
    if refresh and write_baseline:
        print("ERROR: --refresh-cache and --write-baseline are separate steps.", file=sys.stderr)
        return 2
    if getattr(args, "status", False):
        armed, reason = arming_status(baseline_path, args.accept_seeded_baseline)
        print(f"gate armed: {armed} ({reason})")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
                fh.write(f"armed={'true' if armed else 'false'}\n")
        return 0
    check_mode = not refresh and not write_baseline
    tmp = Path(tempfile.mkdtemp(prefix="scribe-gate-"))
    cache: TrackingCache | None = None
    try:
        if check_mode:
            armed, reason = arming_status(baseline_path, args.accept_seeded_baseline)
            if not armed:
                msg = NOT_ARMED_TEXT.format(reason=reason)
                print("=" * 78 + f"\n{msg}\n" + "=" * 78)
                _annotate("warning", "GATE NOT ARMED", msg)
                return 0
        if refresh:
            if providers is None:
                if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
                    raise GateError("--refresh-cache calls the providers and is refused in CI")
                providers = live_providers()
        else:
            removed = _scrub_provider_env()
            if removed:
                print(f"note: ignoring provider key(s) in the environment ({', '.join(removed)}); replay never uses them")
            providers = providers or replay_providers()
        if not args.database_url:
            raise GateError("--database-url is required (a throwaway database whose name contains 'test')")
        require_test_database(args.database_url)
        cache = TrackingCache(tmp / "work.sqlite", providers.mode)
        if cache_path.is_file():
            n = load_cache_file(cache_path, cache)
            print(f"cache: loaded {n} cached provider responses from {cache_path}")
        elif not refresh:
            raise GateCacheMiss(f"cache file {cache_path} does not exist. " + REFRESH_HELP)
        cases = _load_split(dataset_dir, args.split)
        out = asyncio.run(replay_retrieval(
            database_url=args.database_url, corpus_dir=Path(args.corpus), cases=cases, profile_name=args.profile,
            cache=cache, providers=providers, tau=args.tau))
        result = build_result(out=out, cases=cases, dataset_dir=dataset_dir, split=args.split, profile=args.profile,
                              seeded=bool(args.seeded))
        print(f"scored {result['summary']['answerable']} answerable + {result['summary']['unanswerable']} unanswerable "
              f"'{args.split}' cases with profile {args.profile}; cache {cache.stats}; failed docs: "
              f"{result['failed_docs'] or 'none'}")
        if refresh:
            n = export_cache_file(cache, cache_path)
            print(f"cache refreshed: {n} entries -> {cache_path} ({cache_path.stat().st_size / 1024:.0f} KiB). "
                  "Next: scribe-eval gate --write-baseline, then commit both files.")
            return 0
        if write_baseline:
            if result["seeded"] is False and not result["measurement"]:
                raise GateError("refusing to write a baseline from fake models: " + json.dumps(result["models"]))
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(f"baseline written: {baseline_path} ({baseline_path.stat().st_size / 1024:.0f} KiB). "
                  f"Dataset review status: {result['dataset_review_status']}")
            return 0
        baseline = load_baseline(baseline_path)
        if args.out:
            Path(args.out).mkdir(parents=True, exist_ok=True)
            (Path(args.out) / "gate-result.json").write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
        report = compare(baseline, result)
        print(report.format())
        if not report.passed:
            _annotate("error", "Retrieval regression gate", "; ".join(
                f"{c.rule} {c.name}: {c.detail}" for c in report.checks if c.level == "fail"))
            return 1
        return 0
    except GateCacheMiss as exc:
        print(f"GATE FAILED (cache miss): {exc}", file=sys.stderr)
        _annotate("error", "Retrieval gate: cache miss", str(exc))
        return 1
    except GateError as exc:
        print(f"GATE FAILED: {exc}", file=sys.stderr)
        _annotate("error", "Retrieval gate", str(exc))
        return 1
    finally:
        if cache is not None:
            cache.close()
        shutil.rmtree(tmp, ignore_errors=True)

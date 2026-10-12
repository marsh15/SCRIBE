# Scribe 2.0

> A citation-verifiable RAG workspace. Upload private documents, ask questions, open every citation at the exact page or row, and see whether each claim in the answer is supported.

[![Live Demo (Scribe 1.0)](https://img.shields.io/badge/Live-scribe--marsh.vercel.app-00C4A0?style=flat-square&logo=vercel)](https://scribe-marsh.vercel.app)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

**Read this first (honesty box).** Scribe 2.0 is code on branch `scribe2/m0-foundation`. It has **not** been deployed to Vercel, run against Neon, or run against the real Gemini or Voyage APIs. The labeled evaluation set has not been human-reviewed, the CI retrieval gate is not armed, the answerability threshold is uncalibrated, Postgres FORCE RLS is deferred, and task T17 (profile tuning and the milestone report) was not done. No retrieval-quality number has been measured (the only measurements in the repo are synthetic database timings, see the performance summary). Details: [Status and known gaps](#status-and-known-gaps). The live site linked above is the earlier Scribe 1.0 deployment.

## What it is

Scribe 2.0 upgrades the original Scribe app (Next.js 16, Clerk, Drizzle, Neon Postgres with pgvector, Vercel Blob, Vercel AI SDK, Gemini) into a retrieval system whose claims are meant to be measured:

- **Hybrid retrieval owned by a Python service:** dense vectors plus Postgres full-text search, fused by reciprocal rank fusion (k=60), optionally reranked by Voyage rerank behind an adapter.
- **Versioned sources and index generations** with atomic activation, so a new upload never leaves a Source unsearchable.
- **Exact citations** from stored source spans (page, bounding box, CSV row), evidence labels `[E1]`, and **post-stream claim verification**.
- **Personal workspaces** with Postgres row-level security policies (not yet forced; see gaps).
- **An evaluation toolkit:** golden set, metrics CLI, run dashboard, profile compare lab, regression gate, scaling harness.

Design decisions and invariants: [`docs/scribe2/DESIGN.md`](docs/scribe2/DESIGN.md). Code-level tour: [`docs/scribe2/ARCHITECTURE.md`](docs/scribe2/ARCHITECTURE.md). Hand-over notes for the next engineer: [`docs/scribe2/HANDOVER.md`](docs/scribe2/HANDOVER.md).

## Architecture

```mermaid
flowchart LR
  B[Browser] --> N["Next.js on Vercel<br/>Clerk, chat stream, uploads, mints 60 s internal token"]
  N -->|"/api/py/* with token"| P["Python FastAPI<br/>/retrieve /compare /verify /evidence/open /ingest/*"]
  N --> DB[("Postgres + pgvector<br/>RLS policies, FTS, queue functions")]
  P --> DB
  N -->|"direct upload"| BL[("Vercel Blob, private")]
  P -->|"download originals"| BL
  P --> G["Gemini<br/>embeddings, OCR, verifier"]
  P --> V["Voyage rerank"]
  N --> G
  CLI["scribe-eval / scribe-perf (local or GitHub Actions)"] --> DB
```

Query path: authenticate and resolve the workspace in Next.js, call `POST /api/py/retrieve`, one `REPEATABLE READ` transaction, dense and lexical search, RRF, rerank, dedup, parent expansion and packing, answerability gate, evidence bundle with `[E#]` labels. Ingestion path: upload to Blob, leased resumable steps PARSE, OCR, CHUNK, EMBED, ACTIVATE. Details in `docs/scribe2/ARCHITECTURE.md`.

## Features (mapped to the tasks that are done)

Source: `git log` on this branch and `docs/scribe2/HANDOVER.md`. "Done" means code and tests exist; see the status section for what is unproven.

| Area | What exists | Tasks |
|---|---|---|
| Design | Decisions D1-D22, invariants INV-1..16, 12 ADRs (`docs/adr/`), Scribe 1.0 baseline description | T01 |
| Python service | FastAPI in `intelligence/`, 60 s HS256 internal tokens, thin `api/py/index.py` entry, CI workflow | T02 |
| Tenancy | Personal workspaces; every TS query scoped; foreign chat ids answer 404 | T03 |
| Cleanup | Legacy ingestion path removed; legacy upload routes answer 410 | T04 |
| Eval data | 24-document Northwind corpus (5 formats, one scanned PDF), golden set, split assignment, validator | T05, T28 |
| Metrics | Recall/Hit@k, MRR, nDCG@10, P@5, paired bootstrap, run records, response cache; test split refused without `--milestone-report` | T06 |
| 1.0 baseline | Harness `scripts/eval-baseline.ts` (live run **not done**) | T07 |
| Schema | Versioned index tables, FTS columns, HNSW, immutability triggers, seed profiles, legacy backfill script | T08 |
| Parsing and chunking | PDF, Markdown, TXT, DOCX, CSV parsers with exact provenance; parent/child chunker with source spans | T09, T10, T11 |
| Indexing | Gemini embedder adapter (1536 dims), staged idempotent indexer with the activation transaction, `INGEST_ENGINE` flag | T12 |
| Retrieval | Dense (exact and HNSW) and lexical search, RRF, rerank adapter with cache and fake, dedup, packing, five seeded profiles, `/retrieve` | T13, T14 |
| RLS | Policies, `scribe_app` role, SECURITY DEFINER queue functions, `scoped()` helper (policies enabled, **not forced**) | T15 |
| Chat engine | `RETRIEVAL_ENGINE=python\|legacy` (default `legacy`), stage-timeline inspector | T16 |
| Ingestion robustness | Leased resumable steps, tick/sweep routes, retry taxonomy, status UI; new versions while the old stays searchable; soft delete with background purge; OCR for scanned pages; guarded legacy backfill | T18-T22 |
| Citations | Open a citation at the exact page or passage (PDF highlight, text quote, CSV row), access re-checked; source detail tabs (Original, Structure, Chunks, Versions, History) | T23, T24 |
| Evidence | Labeled evidence bundles, answerability gate, cited answers; post-stream claim verification with six statuses | T25, T26 |
| Security | Adversarial suite, IDOR sweep, token matrix, log-capture canaries, [threat model](docs/scribe2/threat-model.md) | T27 |
| Lab and dashboards | Profile compare (2-5 profiles, rank movement); `/eval` run dashboard with failure categories | T29, T30 |
| Regression gate | `scribe-eval gate` and CI job `retrieval-gate` (**not armed**) | T31 |
| Performance | `scribe-perf` harness and [report](docs/scribe2/benchmarks/performance.md) | T32 |
| Release | This README, architecture doc, demo seed and script, benchmark placeholder, fresh-clone check | T33 |

Not done: **T17** (run profiles A-E, calibrate the answerability threshold, choose a default, write the milestone report).

The older product features (billing with Razorpay, plan limits, Supermemory option, three-pane layout, dark mode) are carried over from Scribe 1.0 and unchanged by this work. `FEATURE_BILLING_ENABLED` stays off on Vercel Hobby (non-commercial).

## Status and known gaps

What is unproven or undone, plainly:

- **Nothing has run on Vercel, Neon, live Gemini or live Voyage.** All provider behaviour is tested with fakes or mock transports. There is no measured retrieval quality, latency or cost against real providers.
- **The golden set is not human-reviewed.** 200 cases; 80 were machine-drafted in T28 and are marked unreviewed. No milestone number may be published before review (INV-12).
- **No Scribe 1.0 baseline has been measured** (harness only). `docs/scribe2/benchmarks/baseline-1.0.md` does not exist.
- **The CI retrieval gate is unarmed:** `benchmarks/gate/cache.jsonl.gz` and `benchmarks/gate/baseline.json` do not exist, so the `retrieval-gate` job passes with a `GATE NOT ARMED` warning and protects nothing. The CI workflow has never run on GitHub.
- **`tau` (the answerability threshold) is uncalibrated** (0.0, rejects nothing), so the gate cannot yet return `INSUFFICIENT_EVIDENCE` for weak evidence. Which retrieval profile is best is not measured.
- **FORCE RLS is deferred** (`drizzle/deferred/force_rls.sql`). The table owner still bypasses policies, and no TypeScript caller uses `scoped()`.
- **T17 is not done**, so `RETRIEVAL_ENGINE` and `INGEST_ENGINE` still default to `legacy`, and the legacy chat path lacks the evidence controls (threat model R9).
- The 100,000-chunk performance step was not measured.

What you (the owner) must do, in order:

1. **Review the golden set** starting at `benchmarks/datasets/golden-v1/REVIEW_FIRST.md`.
2. **Put the keys in your shell or `.env.local`** (names below; never commit values).
3. **Vercel preview deploy** following [`docs/scribe2/T02-verification.md`](docs/scribe2/T02-verification.md). If Python does not co-deploy, use a second Vercel project rooted at `intelligence/` (ADR-0002).
4. **Neon branch check**: on a branch copy of production run the migrations (see Quick start), then `npx tsx scripts/backfill-legacy-generations.ts --dry-run` (`tsx` is not a declared dependency: `npx` downloads it on first use); check `select extversion from pg_extension where extname='vector'` (iterative scans need 0.8 or newer, else use `dense_mode=exact`). The Neon paths of the baseline script and the backfill have never run.
5. **RLS rollout order** (HANDOVER section 8): apply migration `0013`; move callers to `scoped()` and a `scribe_app` DSN (enable LOGIN out of band); only then run `drizzle/deferred/force_rls.sql`. Never run `drizzle-kit push` against a database that has these policies.
6. **Retire legacy data** when satisfied: `uv run python -m scribe_ingest.upgrade backfill|progress` (from `intelligence/`) queues the upgrade; `drizzle/deferred/retire_legacy.sql` (guarded, manual, supports `-v dry_run=1`) deletes legacy 3072-dim embeddings of upgraded Sources.
7. **Run the live Scribe 1.0 baseline** ([`baseline-1.0-howto.md`](docs/scribe2/benchmarks/baseline-1.0-howto.md); needs Node 18), do T17, then fill [`docs/scribe2/benchmarks/final.md`](docs/scribe2/benchmarks/final.md) (currently a placeholder).
8. **Arm the gate** with the steps in [`docs/scribe2/release-rules.md`](docs/scribe2/release-rules.md), then make `retrieval-gate` a required check on `main`.
9. **Local Docker note.** `docker compose up -d db` starts `pgvector/pgvector:pg17` on port 54329. On the author's Mac (OrbStack) the runs recorded in this repo used a container named `scribe-db-bridge` on the same port instead of the compose-created one; the compose network problem was not investigated. If the database is unreachable, start OrbStack (`open -a OrbStack`) and check the container.

## Quick start

Prerequisites: Node 22 (CI uses 22; vitest was run on Node 26; Node 18 is needed only for the 1.0 baseline script), [uv](https://docs.astral.sh/uv/) with Python 3.12, Docker, `psql`.

```bash
git clone https://github.com/marsh15/SCRIBE.git && cd SCRIBE
git checkout scribe2/m0-foundation

docker compose up -d db            # Postgres + pgvector on localhost:54329 (user/password/db: scribe)
npm ci
(cd intelligence && uv sync)

# Throwaway/test database schema (DROPS and recreates schema public: never point this at real data):
scripts/apply-migrations.sh postgresql://scribe:scribe@localhost:54329/scribe

cp .env.example .env.local         # then fill in values yourself (see variable names below)
npm run dev                        # Next.js dev server (webpack)
```

`scripts/apply-migrations.sh` exports the schema from `lib/db-schema.ts` with `drizzle-kit export` and replays the hand-written migrations `0010` onward, because `0000`-`0009` cannot be replayed on an empty database (ADR-0003). For an existing database that already has `0000`-`0007` applied, the journaled migrations are applied with `npx drizzle-kit migrate` (not run in this task; try it on a Neon branch first). The Python service is not started by `npm run dev`; it is reached in deployment through `/api/py/*`. Locally, tests call it in-process.

### Environment variable names (values are never in the repo)

Web app (`lib/env.ts` and direct `process.env` reads): `NEON_DATABASE_URL`, `GOOGLE_GENERATIVE_AI_API_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `BLOB_READ_WRITE_TOKEN`, `UPLOAD_SIGNING_SECRET`, `CRON_SECRET`, `INTERNAL_SERVICE_SECRET`, `VERCEL_AUTOMATION_BYPASS_SECRET`, `INTELLIGENCE_BASE_URL`, `VERCEL_URL`, `NEXT_PUBLIC_APP_URL`, `INGEST_ENGINE` (`python` or `legacy`, default `legacy`), `RETRIEVAL_ENGINE` (`python` or `legacy`, default `legacy`), `ENABLE_SUPERMEMORY`, `SUPERMEMORY_API_KEY`, `ENABLE_RAG_EVALS`, `GOOGLE_CHAT_MODEL`, `GOOGLE_RAG_JUDGE_MODEL`, `FEATURE_BILLING_ENABLED`, `FEATURE_ASYNC_INGESTION_ENABLED`, `FEATURE_PUBLIC_LANDING_ENABLED`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_PLAN_PRO_INR`, `RAZORPAY_PLAN_TEAM_INR`, `RAZORPAY_MANAGE_BILLING_URL`.

Python service and CLIs (`intelligence/src`): `INTERNAL_SERVICE_SECRET`, `DATABASE_URL` (falls back to `NEON_DATABASE_URL`), `GOOGLE_GENERATIVE_AI_API_KEY`, `VOYAGE_API_KEY`, `BLOB_READ_WRITE_TOKEN`, `GOOGLE_CHAT_MODEL`, `SCRIBE_CACHE_MODE`, `SCRIBE_EMBEDDER`, `SCRIBE_ALLOW_FAKE_EMBEDDINGS` (tests only), `INGEST_STEP_BUDGET_S`, `INGEST_TIME_BUDGET_S`, `PURGE_BUDGET_S`, `PURGE_GRACE_SECONDS`. Tests: `DATABASE_URL_TEST`.

## Running the tests

```bash
# TypeScript (repo root)
npx vitest run          # same as npm test
npx tsc --noEmit        # same as npm run typecheck
npm run lint
npm run check           # lint + typecheck + test + next build (build needs Clerk keys; CI uses placeholders)

# Python (needs the database from docker compose and a schema from apply-migrations.sh)
cd intelligence
DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/<db with "test" in its name> uv run pytest -q
```

Without `DATABASE_URL_TEST` the database tests are skipped. Test suites by directory: `tests/ingestion` (parsers, chunker, OCR, embedder), `tests/retrieval` (dense, lexical, RRF, rerank, pipeline, compare, scoping), `tests/integration` (indexer, ingestion steps, versions, delete and purge, evidence, verification, dashboard), `tests/security` (adversarial documents, IDOR, token matrix, log canaries, RLS), `tests/evaluation` (metrics, gate, answer scoring, demo seed), `tests/perf`, `tests/schema`.

Last full runs on this branch (T33, 2026-10-12, one Mac): **Python 1740 passed, 0 skipped** (with `DATABASE_URL_TEST`, 321 s); **vitest 403 passed, 3 skipped** (the 3 need a database) across 47 files. These are counts of tests passing, not quality measurements.

## Evaluation workflow

```bash
uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1
uv run --project intelligence scribe-eval score --rankings R.jsonl --dataset benchmarks/datasets/golden-v1 --split dev --profile NAME
uv run --project intelligence scribe-eval compare <run dir A> <run dir B>
uv run --project intelligence scribe-eval answer --rankings R.jsonl --split dev        # answer-level scoring
uv run --project intelligence scribe-eval ingest-corpus --corpus fixtures/corpus --embedder gemini --database-url <EVAL DB> --reset
uv run --project intelligence scribe-eval publish-run <run dir> --database-url <EVAL DB>   # feeds the /eval dashboard
uv run --project intelligence scribe-eval gate --status                                  # is the CI gate armed?
```

- Rankings file format: [`docs/scribe2/benchmarks/rankings-format.md`](docs/scribe2/benchmarks/rankings-format.md). The test split is refused unless `--milestone-report` is given (INV-12).
- 1.0 baseline: [`baseline-1.0-howto.md`](docs/scribe2/benchmarks/baseline-1.0-howto.md). Distractor corpus (300 hard-negative documents, regenerated locally): [`distractors.md`](docs/scribe2/benchmarks/distractors.md).
- Gate rules and arming: [`docs/scribe2/release-rules.md`](docs/scribe2/release-rules.md) and [`benchmarks/gate/README.md`](benchmarks/gate/README.md).
- Final report: [`docs/scribe2/benchmarks/final.md`](docs/scribe2/benchmarks/final.md), **placeholder, not measured**.
- Scaling harness: `uv run --project intelligence scribe-perf --help` (commands in `performance.md` section 11).

## Demo data

`scripts/demo-seed/demo_seed.py` loads the Northwind demo corpus into a **new** account's personal workspace through the same indexer the service uses. Dry run by default; refuses production-looking databases without `--i-know`.

```bash
uv run --project intelligence python scripts/demo-seed/demo_seed.py \
  --database-url <DEMO DB URL> --user-id <Clerk user id of the new account>            # dry run, no connection
# add --apply to write (needs GOOGLE_GENERATIVE_AI_API_KEY for embeddings and OCR)
```

Scene-by-scene demo plan, with what must be live-verified first: [`docs/scribe2/demo-script.md`](docs/scribe2/demo-script.md).

## Security summary

Workspace boundary on every row (policies enabled, not forced), 60-second audience-bound internal tokens, document text treated as untrusted data (fixed markers, one server-scoped tool, labels issued by the server), "Verified" only when deterministic checks and the verifier agree, and no document text in logs. An adversarial suite and IDOR sweep hold these in place. This is an engineering review with tests, **not** a penetration test, and the injection tests prove what the code does with hostile text, not how a real model behaves. Open residual risks (FORCE RLS, a single static internal secret, no rate limiting on `/api/py/*`, uncalibrated gate, and others): [`docs/scribe2/threat-model.md`](docs/scribe2/threat-model.md) section 6.

## Performance summary

Source: [`docs/scribe2/benchmarks/performance.md`](docs/scribe2/benchmarks/performance.md) (raw: `benchmarks/perf/results-m1-orbstack.json`). **Synthetic vectors and text, one Mac laptop (M1, Postgres 17.11 and pgvector 0.8.7 in an OrbStack VM), scales 1,000, 10,000 and 30,000 chunks only. Not Neon, not Vercel, not real embeddings. 100,000 chunks was loaded but not measured.** Use for shape, not as promises.

- Exact dense search cost about 14 microseconds per eligible chunk and grows linearly: p50 9.9 ms at 700 chunks, 79 ms at 7,000, 300 ms at 21,000 (vector(1536), k=40).
- The shipped `ann` SQL did not use the HNSW index in any measured case (0 of 15 combinations); it was still 1.3x to 1.7x faster than exact for workspaces of 700 chunks or more, with recall@10 = 1.0.
- With HNSW forced, it was 3x to 3.4x faster than exact for a workspace that is most of the table (21,000 of 30,000 chunks) and slower than exact for a small workspace in a large table (300 of 30,000: 78 ms vs 7 ms).
- With `hnsw.iterative_scan` off, a selective filter returned 16.5 of 40 rows (recall@10 0.84); with `relaxed_order` it returned 40 rows (recall@10 0.99 to 1.0).
- halfvec(1536) halves storage (3,076 vs 6,148 bytes per embedding) with exact-halfvec vs exact-vector recall@10 of 0.999 to 1.0.
- `AUTO_ANN_THRESHOLD` stays 20,000.

## Tech stack

Next.js 16.1 (App Router) and React 19; Tailwind CSS v4 with Radix UI and shadcn/ui; Neon Postgres with Drizzle ORM and pgvector; Clerk; Vercel AI SDK with Gemini; Python 3.12 with FastAPI, psycopg 3, pdfplumber, python-docx, pypdfium2; Voyage rerank; Razorpay (inherited); Vercel Blob; Vercel Hobby.

## Repository map

```
app/                 Next.js routes: chat, files/[id] (source detail), compare, eval, upload, billing, api/*
components/ lib/     UI components; server helpers (workspace, internal token, evidence, ingestion intake, storage)
api/py/index.py      thin Vercel entry for the Python service (rewrite /api/py/* in vercel.json)
intelligence/src/    scribe_intel (service), scribe_ingest (parsers, chunker, indexer), scribe_eval, scribe_perf
drizzle/             migrations 0000-0018; deferred/ (guarded manual SQL); rollback/
benchmarks/          golden-v1 dataset, gate/ (cache and baseline absent), perf/ results
fixtures/            Northwind corpus (24 docs), adversarial documents, distractor generators
scripts/             apply-migrations.sh, eval-baseline.ts, backfill-legacy-generations.ts, demo-seed/
docs/                adr/, scribe2/ (design, architecture, handover, threat model, benchmarks, demo script)
```

## Deployment

Target: one Vercel Hobby project plus Neon and Blob (`vercel.json` has the Python rewrite and two daily crons: `/api/internal/ingest/run` at 00:00 and `/api/internal/ingest/sweep` at 01:00). Ingestion re-fires itself between steps; the crons are only a safety net. Scribe 2.0 has **not** been deployed; follow the status list above.

## License

MIT

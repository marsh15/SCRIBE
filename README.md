# Scribe

A retrieval-augmented question-answering workspace for private documents. Users upload PDF, DOCX, CSV, Markdown and text files, ask questions, and get answers with citations that open at the supporting page, passage or CSV row. After the answer streams, each claim is checked against the evidence it cites.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

The code in this repository is **Scribe 2.0**, a rebuild of the retrieval and ingestion layers of the original Scribe app. Read [Status](#status) before relying on it: parts of 2.0 have never run against the production providers, and production still serves the pre-2.0 build.

## Demo

![Scribe demo: a question about annual leave, the answer citing the 2026 policy, the citation opened at its page, and the claim verdicts](docs/demo/demo.gif)

[Watch the full-quality video with sound](docs/demo/demo.mp4) (22 seconds, 1080p H.264; GitHub opens its own player). The clip above is the same video as a looping GIF, because GitHub does not render `<video>` tags that point at repository files.

It is an animated walkthrough built from the app's UI strings and styles and the fictional Northwind fixture PDFs: a question about annual leave, the answer citing the 2026 policy, the citation opened at its page, and the claim verdicts. It is not a recording of a running deployment.

## Contents

[Problem](#problem-and-approach) · [Status](#status) · [Architecture](#architecture) · [Features](#implemented-features) · [Tech stack](#tech-stack) · [Getting started](#getting-started) · [Configuration](#configuration) · [Database](#database-and-migrations) · [Commands](#commands) · [Testing](#testing) · [Evaluation](#evaluation) · [Deployment](#deployment) · [Design decisions](#design-decisions-and-trade-offs) · [Limitations](#known-limitations) · [Security](#security) · [Documentation](#documentation) · [Repository layout](#repository-layout)

## Problem and approach

Answers from a language model are only useful on private documents if the reader can tell where each statement came from and whether the source actually says it. Scribe's approach:

- Retrieval over the user's own documents on every question. The chat model has one tool, search, and is told to answer only from what it returns.
- Citations point at stored source spans (page and bounding box, text quote, or CSV row), not at whole files. Opening a citation re-checks access and highlights the exact location.
- Each answer sentence gets a verification status after streaming. `VERIFIED` requires both deterministic checks (the label exists in this turn's evidence, the quote hash matches) and a verifier model returning `SUPPORTED`.
- Retrieval quality is measured, not assumed. The repo includes a labeled question set, a metrics CLI, a profile comparison lab and a CI regression gate.

## Status

| Area | State |
|---|---|
| Code | Scribe 2.0 is merged to `main` (PRs #10 and #11). |
| Production | Not promoted. Merging PR #10 auto-deployed to Vercel on 2026-10-12 and broke production, because migrations `0008` and later had never run on Neon. Production was rolled back to the pre-merge build and Vercel production auto-assign was paused. The live site at [scribe-marsh.vercel.app](https://scribe-marsh.vercel.app) is that older build. Do not promote a `main` deployment before the migration rehearsal in [`HANDOVER.md`](docs/scribe2/HANDOVER.md) section 11. |
| Engine flags | `INGEST_ENGINE` and `RETRIEVAL_ENGINE` both default to `legacy` (the original TypeScript pipeline). The Python pipeline described below is opt-in. |
| Providers | Gemini has been used for the Scribe 1.0 baseline and a preliminary run of three retrieval profiles on a local database. Voyage rerank has never been called (no key). Neon has never been used with the 2.0 schema. |
| Evaluation | The 200-case golden set is not human-reviewed (80 cases are machine-drafted). Results in [`m1-prelim.md`](docs/scribe2/benchmarks/m1-prelim.md) are labelled preliminary and are not quoted here. The final milestone report is a placeholder. |
| CI | The `node`, `python` and `retrieval-gate` jobs pass on GitHub. The gate is not armed (no baseline is committed), so it checks nothing yet. |
| Row-level security | Policies exist and are enabled, but not forced. The table-owner connection used today bypasses them, so isolation currently rests on application-level workspace filters plus tests. |

The full list of gaps is under [Known limitations](#known-limitations).

## Architecture

![Scribe 2.0 architecture: browser, Next.js on Vercel, Python FastAPI service, Neon Postgres with pgvector, Vercel Blob, Gemini, Voyage rerank, Clerk, and the offline eval tooling](docs/assets/architecture.svg)

The diagram is generated from [`scripts/gen_architecture_diagram.py`](scripts/gen_architecture_diagram.py). The editable source is [`docs/assets/architecture.excalidraw`](docs/assets/architecture.excalidraw) (open it at excalidraw.com); change the tables in the script and re-run it to regenerate both files.

### Components

| Component | Location | Role |
|---|---|---|
| Next.js 16 app | `app/`, `components/`, `lib/`, `proxy.ts` | UI, Clerk authentication, chat streaming through the Vercel AI SDK, private upload intake, billing, internal cron and tick routes |
| Python service | `intelligence/src/scribe_intel`, entry `api/py/index.py` | FastAPI app on a Vercel Python function, mounted at `/api/py/*`. Retrieval, compare, evidence open, claim verification, ingestion steps |
| Ingestion library | `intelligence/src/scribe_ingest` | PDF, DOCX, CSV, Markdown and text parsers, OCR client, chunker, embedder, indexer, purge |
| Eval and perf CLIs | `intelligence/src/scribe_eval`, `scribe_perf` | `scribe-eval` and `scribe-perf`. Run locally or in GitHub Actions, never on Vercel |
| Postgres | `lib/db-schema.ts`, `drizzle/` | Single store: relational data, pgvector embeddings, full-text search, RLS policies, queue functions |
| Object storage | Vercel Blob (private) | Original bytes of every source version |

### Query path

1. The browser posts to `/api/chat`. Clerk middleware authenticates the request and `resolveWorkspace()` returns the user's personal workspace.
2. The model (`gemini-2.5-flash` unless `GOOGLE_CHAT_MODEL` is set) gets one tool, `searchKnowledgeBase`, with a cap of 3 steps per turn.
3. With `RETRIEVAL_ENGINE=legacy` the tool runs a pgvector cosine search in TypeScript (top 10, similarity threshold 0.3). With `RETRIEVAL_ENGINE=python` it calls `POST /api/py/retrieve` with a 60-second HS256 token carrying `sub`, `wid` (workspace), `scope` and `aud=scribe-intel`.
4. The Python service runs one `REPEATABLE READ READ ONLY` transaction with `app.workspace_id` set. It embeds the query, runs dense search (top 40) and Postgres full-text search, fuses the lists with reciprocal rank fusion (k=60), reranks the top 20 with Voyage `rerank-2.5`, removes duplicates, expands to parent sections and packs up to 6,000 tokens. The result is an evidence bundle with labels `[E1]`, `[E2]`, and so on.
5. If the reranker fails, the fused order is used and the response is marked `degraded: rerank_unavailable`. There is no silent fallback from the Python engine to the legacy one.
6. After the answer streams, `lib/chat-verify-stream.ts` splits it into claims and calls `/api/py/verify`. The result arrives as a `data-verification` stream part. Claim verification runs only with the Python engine.

### Ingestion path

1. `POST /api/sources/reserve` checks plan and size limits, inserts a `files` row and returns a Vercel Blob client token. The browser uploads straight to Blob.
2. Blob calls `/api/sources/upload-complete`. One SQL statement marks the source `queued` and inserts an `ingestion_jobs` row. With `INGEST_ENGINE=python` a tick is fired.
3. `/api/internal/ingest/tick` (authenticated with `CRON_SECRET`) calls Python `/ingest/step`. A step claims one job through a `SECURITY DEFINER` function using `FOR UPDATE SKIP LOCKED`, with a lease token. It runs PARSE, OCR (only for pages with almost no text), CHUNK, EMBED (batched, resumable) and ACTIVATE, and re-fires the tick while work remains. All writes are fenced by the lease token.
4. ACTIVATE is one transaction that switches the active index generation for the source and deletes the superseded generation's embeddings. A new version never makes the old one unsearchable until it is ready.
5. The two daily Vercel crons (`ingest/run`, `ingest/sweep`) are a safety net: `sweep` requeues expired leases and purges soft-deleted sources after a 24-hour grace period.

With `INGEST_ENGINE=legacy` the TypeScript worker does extraction, 4,000-character chunking and 3,072-dimension embeddings inline.

### Trust boundaries

- `proxy.ts` makes `/api/py/*` publicly routable, because the Python service has no Clerk session. Every Python route instead verifies the internal token (HS256, audience `scribe-intel`, required scope) and takes the workspace only from the token or the job row, never from a request body.
- The browser never sees the internal token. Internal cron and tick routes require `Authorization: Bearer $CRON_SECRET` (constant-time compare).
- Foreign chat, evidence, file and source ids return 404. `__tests__/security/` enumerates every route and fails when a new one is not classified.

More detail, including the data model: [`docs/scribe2/ARCHITECTURE.md`](docs/scribe2/ARCHITECTURE.md).

## Implemented features

| Area | What exists |
|---|---|
| Sources | Private upload (PDF, DOCX, CSV, Markdown, TXT), per-user plan and size limits, status states (uploading, queued, processing, retrying, ready, failed), retry, soft delete with background purge |
| Versions | Upload a new version of a source while the old one stays searchable; identical bytes are recorded as `duplicate_of`; Python engine only |
| Parsing | pdfplumber, python-docx, CSV with delimiter and BOM handling. Limits: 300 pages, 50 OCR pages, 5,000 CSV rows per source. Scanned pages are rendered with pypdfium2 and transcribed by Gemini 2.5 Flash |
| Chunking | Child chunks up to 384 tokens inside section parents, with source spans back to the original blocks. Token counts use tiktoken `o200k_base` as a recorded proxy |
| Retrieval | Dense (exact scan or HNSW), Postgres FTS (`fts_en`, `fts_simple`), RRF, rerank adapter, dedup, parent expansion, packing; five seeded profiles from dense-only to hybrid with rerank |
| Evidence | Labels `[E#]` unique per turn, stored quotes and spans, an answerability gate (threshold uncalibrated, see limitations), post-stream claim verification with six statuses |
| Citation viewer | `/files/[id]` source detail with Original, Structure, Chunks, Versions and History tabs; PDF highlight, text quote and CSV row highlight |
| Retrieval lab | `/compare` runs 2 to 5 profiles on one query and shows rank movement |
| Eval dashboard | `/eval` lists runs with failure categories; visible only to users who own or belong to an `eval` workspace |
| Inspector | Right-hand pane showing per-stage timings and retrieved chunks for the last answer |
| Billing | Razorpay checkout, webhook, plans and usage metering, all behind `FEATURE_BILLING_ENABLED` (off) |

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Web | Next.js 16 (App Router, webpack dev server), React 19, Tailwind CSS v4, shadcn/ui on Radix | Existing app; server routes keep Clerk, uploads and chat streaming in one place |
| Auth | Clerk | Hosted sessions; the Clerk user id is the identity everywhere |
| Chat | Vercel AI SDK with `@ai-sdk/google` (Gemini 2.5 Flash default) | Streaming UI messages and tool calls |
| Retrieval service | Python 3.12, FastAPI, psycopg 3 | Same code serves chat and offline evaluation, so measured behaviour is the shipped behaviour |
| Database | Neon Postgres, pgvector, Drizzle ORM | One store for rows, vectors and full-text search; Drizzle is the only migration owner |
| Embeddings | `gemini-embedding-2` at 1,536 dimensions (2.0); `gemini-embedding-2-preview` at 3,072 (legacy) | 1,536 dimensions fit an HNSW index (pgvector caps HNSW at 2,000) |
| Rerank | Voyage `rerank-2.5` behind an adapter, with a cache and a fake for tests | Replaceable provider |
| Files | Vercel Blob, private | Direct browser upload without proxying bytes through a function |
| Hosting | One Vercel Hobby project | Functions up to 300 s, once-daily cron; ingestion is therefore resumable steps |
| Billing | Razorpay (INR/USD plans) | Inherited from 1.0; off |

## Getting started

Prerequisites: Node 22, [uv](https://docs.astral.sh/uv/) (Python 3.12 is pinned in `intelligence/.python-version`), Docker, `psql`. CI uses Node 22. The Scribe 1.0 baseline script needs Node 18 because `pdf-parse` fails on valid PDFs under newer Node versions.

```bash
git clone https://github.com/marsh15/SCRIBE.git && cd SCRIBE
npm ci
(cd intelligence && uv sync)

docker compose up -d db        # pgvector/pgvector:pg17 on localhost:54329, user/password/db "scribe"
cp .env.example .env.local     # fill in values; see Configuration

# Disposable database only: DROPS schema public, rebuilds it from lib/db-schema.ts, replays migrations 0010+.
scripts/apply-migrations.sh postgresql://scribe:scribe@localhost:54329/scribe

npm run dev                    # http://localhost:3000
```

The app needs real Clerk keys, a Neon (or local Postgres) URL, a Gemini key and a Blob token to do anything useful. The Python service is not started by `npm run dev`: in deployment it is reached through the `/api/py/*` rewrite, and the Python tests call it in-process. To run it standalone, `cd intelligence && uv run uvicorn scribe_intel.app:app` (uvicorn is a dev dependency); set `INTERNAL_SERVICE_SECRET` and `DATABASE_URL`, and point `INTELLIGENCE_BASE_URL` at it.

On the author's Mac the local runs used a container named `scribe-db-bridge` on port 54329 instead of the compose-created one, because of a compose network problem that was not investigated. If the database is unreachable, check that Docker (OrbStack) is running.

## Configuration

Values are never committed. `.env.example` lists the web app's names; Python-only names are read from the process environment. "Required" means a feature fails without it.

**Web app**

| Variable | Required | Purpose |
|---|---|---|
| `NEON_DATABASE_URL` | yes | Postgres connection (Drizzle, neon-http). The Python service falls back to it when `DATABASE_URL` is unset |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` | yes | Clerk. `next build` also needs them; CI uses placeholders |
| `GOOGLE_GENERATIVE_AI_API_KEY` | yes | Chat model, legacy embeddings. `/api/chat` returns 503 without it |
| `BLOB_READ_WRITE_TOKEN` | yes | Vercel Blob uploads and downloads |
| `CRON_SECRET` | yes for ingestion | Bearer secret for `/api/internal/ingest/{run,sweep,tick}` |
| `INTERNAL_SERVICE_SECRET` | for the Python engine | Signs and verifies the 60-second internal token |
| `INTELLIGENCE_BASE_URL` | no | Base URL for `/api/py` calls; defaults to `https://$VERCEL_URL`, then `http://localhost:3000` |
| `VERCEL_AUTOMATION_BYPASS_SECRET` | previews | Lets internal calls pass Vercel deployment protection |
| `INGEST_ENGINE` | no | `legacy` (default) or `python` |
| `RETRIEVAL_ENGINE` | no | `legacy` (default) or `python` |
| `GOOGLE_CHAT_MODEL` | no | Overrides `gemini-2.5-flash` |
| `NEXT_PUBLIC_APP_URL` | no | Absolute URL for upload callbacks and metadata; falls back to the request origin and a hardcoded default |
| `FEATURE_BILLING_ENABLED` | no | Default `false`. Gates Razorpay checkout, usage metering and plan limits |
| `FEATURE_PUBLIC_LANDING_ENABLED` | no | Default `true`. Controls the `/` to `/chat` redirect |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_PLAN_PRO_INR`, `RAZORPAY_PLAN_TEAM_INR`, `RAZORPAY_MANAGE_BILLING_URL` | only with billing | Razorpay integration |
| `ENABLE_SUPERMEMORY`, `SUPERMEMORY_API_KEY` | no | Supermemory wrapper; ignored when `RETRIEVAL_ENGINE=python` |
| `ENABLE_RAG_EVALS`, `GOOGLE_RAG_JUDGE_MODEL` | no | LLM-judge scoring of legacy-engine answers |

**Python service and CLIs**

| Variable | Purpose |
|---|---|
| `INTERNAL_SERVICE_SECRET` | Verifies internal tokens. Without it every request is rejected |
| `DATABASE_URL` | Postgres DSN (falls back to `NEON_DATABASE_URL`) |
| `GOOGLE_GENERATIVE_AI_API_KEY` | Embeddings, OCR and the claim verifier |
| `VOYAGE_API_KEY` | Rerank. Without it rerank profiles fail or degrade |
| `BLOB_READ_WRITE_TOKEN` | Downloading originals and purging blobs |
| `SCRIBE_EMBEDDER`, `SCRIBE_OCR` | `gemini` (default) or `fake`. `fake` also needs `SCRIBE_ALLOW_FAKE_EMBEDDINGS=1` and a database whose name contains `test` |
| `SCRIBE_RERANKER` | `voyage` (default), `fake` or `none` |
| `SCRIBE_CACHE_MODE` | Controls the embedding and rerank response cache used by the eval tooling |
| `INGEST_TIME_BUDGET_S` (270), `INGEST_STEP_BUDGET_S` (240), `PURGE_GRACE_SECONDS` (86400), `PURGE_BUDGET_S` (45) | Time budgets; defaults in parentheses |
| `DATABASE_URL_TEST` | Throwaway database for Python tests; its name must contain `test` |

TypeScript tests also read `TEST_DATABASE_URL` in two baseline-related suites, and `scripts/eval-baseline.ts` reads `BASELINE_NODE_DIR`.

## Database and migrations

`lib/db-schema.ts` is the Drizzle schema and the source of truth. SQL lives in `drizzle/`, journaled in `drizzle/meta/_journal.json` (`0000` to `0019`).

- `0000`-`0009` are the original Scribe history and cannot be replayed on an empty database (for example `0004` assumes columns earlier files never create).
- `0010`-`0019` are hand-written and idempotent: source versions, profiles, index generations, chunks, retrieval, evidence and eval tables (`0010`), seed profiles (`0011`, `0012`), RLS roles and policies (`0013`), resumable ingestion (`0014`), version uploads (`0015`), soft delete and purge (`0016`), OCR pages (`0017`), legacy upgrade state (`0018`), composite workspace foreign keys (`0019`).
- `drizzle/rollback/` holds `.down.sql` files for `0013` to `0019`. `drizzle/deferred/` holds guarded, manual, non-journaled SQL (`force_rls.sql`, `retire_legacy.sql`).
- Embeddings: `chunk_embeddings_1536` (HNSW, cosine) for 2.0 and `chunk_embeddings_3072` (legacy, exact search only, since HNSW is capped at 2,000 dimensions).

For a **test or throwaway** database, `scripts/apply-migrations.sh <url>` exports the schema from `lib/db-schema.ts` with `drizzle-kit export` and replays `0010+`. `scripts/test-migrations-nosuperuser.sh` proves the migrations apply twice as a non-superuser that owns the database, as on a managed Postgres.

For an **existing** database, use `npx drizzle-kit migrate` and rehearse it on a copy first (a Neon branch). Never run `drizzle-kit push` against a database that has the RLS objects: Drizzle does not model them and could drop them.

## Commands

```bash
# Web app
npm run dev          # next dev --webpack
npm run build        # next build (needs Clerk keys; CI uses placeholders)
npm run lint         # eslint
npm run typecheck    # tsc --noEmit
npm test             # vitest run
npm run check        # lint + typecheck + test + build

# Python (from intelligence/)
uv sync
DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/<name containing test> uv run pytest -q

# Evaluation and performance (from the repo root)
uv run --project intelligence scribe-eval --help     # validate, assign-splits, score, answer, judge-sheet,
                                                     # judge-agreement, compare, publish-run, gate,
                                                     # ingest-corpus, run-profile
uv run --project intelligence scribe-perf --help     # run, render, drop

# Other scripts
scripts/apply-migrations.sh <url>
scripts/test-migrations-nosuperuser.sh [admin-url]
npx tsx scripts/backfill-legacy-generations.ts --dry-run   # tsx is not a dependency; npx downloads it
(cd intelligence && uv run python -m scribe_ingest.upgrade backfill|progress)
uv run --project intelligence python scripts/demo-seed/demo_seed.py --help
python3 scripts/gen_architecture_diagram.py
```

## Testing

- **TypeScript:** `npm test` runs vitest (Node environment, jsdom for `.tsx` files that opt in). A run on this branch passed 416 tests and skipped 7 that need a database. `__tests__/security/` holds the route-table guard, the IDOR sweep, proxy access rules, log-capture canaries and adversarial prompt-injection documents.
- **Python:** `uv run pytest` collects 1,781 tests across `tests/ingestion`, `retrieval`, `integration`, `security`, `evaluation`, `perf` and `schema`. Database-backed tests are skipped unless `DATABASE_URL_TEST` points at a database whose name contains `test`. They were not re-run for this documentation pass; CI runs them against a pgvector service container.
- **CI** (`.github/workflows/ci.yml`): `node` (`npm run check`), `python` (non-superuser migration check, schema replay, `pytest`) and `retrieval-gate` (replay of cached provider responses, no keys). The gate passes with a "GATE NOT ARMED" warning until a baseline is committed ([`release-rules.md`](docs/scribe2/release-rules.md)).

Passing tests show that code behaves as specified against fakes or recorded responses. They are not retrieval-quality measurements.

## Evaluation

The evaluation set is a synthetic 24-document corpus (`fixtures/corpus/`, five formats plus a scanned PDF) and a 200-case golden set (`benchmarks/datasets/golden-v1/`) across ten categories, split 40/20/40 into dev, validation and test. The test split is refused by the CLI unless `--milestone-report` is passed, so it is used once rather than for tuning.

```bash
uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1
uv run --project intelligence scribe-eval ingest-corpus --corpus fixtures/corpus --embedder gemini \
  --database-url <EVAL DB> --reset
uv run --project intelligence scribe-eval run-profile --profile hybrid-rrf --split dev \
  --database-url <EVAL DB> --out benchmarks/runs/m1/hybrid-rrf.dev.jsonl
uv run --project intelligence scribe-eval score --rankings benchmarks/runs/m1/hybrid-rrf.dev.jsonl \
  --split dev --profile hybrid-rrf --out benchmarks/runs/m1sc
uv run --project intelligence scribe-eval compare <run dir A> <run dir B>
uv run --project intelligence scribe-eval gate --status
```

Metrics are Recall@k, Hit@k, MRR, nDCG@10 and P@5 with paired bootstrap confidence intervals. Reports and formats: [`docs/scribe2/benchmarks/`](docs/scribe2/benchmarks/).

## Deployment

The intended target is one Vercel Hobby project with Neon and Vercel Blob. [`vercel.json`](vercel.json) defines the `/api/py/*` rewrite, the Python function (`maxDuration` 300, bundles `intelligence/src/**`) and two daily crons (`/api/internal/ingest/run` at 00:00 and `/api/internal/ingest/sweep` at 01:00). Python dependencies on Vercel come from the root `requirements.txt`, which must be kept in sync by hand with `intelligence/pyproject.toml`.

Hobby limits drive the design: functions run at most 300 seconds and cron runs once a day, so ingestion is leased, resumable steps that re-trigger themselves. Hobby is non-commercial, so `FEATURE_BILLING_ENABLED` stays off.

Not verified: that the Python function co-deploys with Next.js on Hobby (checklist in [`T02-verification.md`](docs/scribe2/T02-verification.md); the fallback is a second Vercel project rooted at `intelligence/`, ADR-0002). Before any production promotion:

1. Rehearse migrations `0008`-`0019` on a Neon branch copy of production. Check `drizzle.__drizzle_migrations`, the Postgres version, `pgvector` 0.8 or newer for iterative HNSW scans, and whether the app's database role has `BYPASSRLS`.
2. Deploy with `INGEST_ENGINE=legacy` and `RETRIEVAL_ENGINE=legacy` first, then switch to `python` on a preview.
3. Enable RLS enforcement in the order given in [`HANDOVER.md`](docs/scribe2/HANDOVER.md) section 8.

## Design decisions and trade-offs

The full list (D1-D22, invariants INV-1 to INV-16) is in [`docs/scribe2/DESIGN.md`](docs/scribe2/DESIGN.md) and the ADRs in [`docs/adr/`](docs/adr/). The ones that shape the code:

- **Python owns retrieval.** One implementation serves chat and offline evaluation. The cost is a second runtime and a service-to-service token.
- **Postgres does everything.** Vectors, full-text search and rows share a store and a transaction, which gives snapshot-consistent retrieval and atomic index activation. The cost is that scaling dense search past tens of thousands of chunks per workspace is a Postgres tuning problem. Performance measurements are in [`performance.md`](docs/scribe2/benchmarks/performance.md); the shipped ANN query was measured not to use the HNSW index, so `auto` mode stays on exact scans below 20,000 chunks.
- **Lexical search is Postgres FTS, not BM25** (ADR-0005), and is named that way.
- **Fusion uses ranks only** (RRF), never added scores from different rankers. The answerability gate uses the reranker's score, never cosine similarity.
- **Light parsers plus Gemini OCR instead of Docling**, to fit the 500 MB Vercel Python bundle (ADR-0004).
- **Source versions and index generations** are immutable and activated in one transaction, so a failed re-index never removes searchable content.
- **Workspaces with RLS** (ADR-0007): every table carries `workspace_id`, policies compare it with `app.workspace_id`, and composite foreign keys stop cross-workspace references.
- **Verification is conservative.** The verifier sees only the stored quote, not the expanded parent text, so a claim supported by sibling text becomes `PARTIAL` or `UNSUPPORTED` rather than `VERIFIED`.
- **Evaluation labels are independent of any index profile** (quote-based), so legacy and new pipelines are scored on the same labels.

## Known limitations

- Nothing in 2.0 has run on Neon, on Vercel, or against live Voyage. Provider behaviour is covered by fakes and mock transports, apart from the local Gemini runs noted under [Status](#status).
- The golden set is unreviewed, so no retrieval-quality figure is publishable. There is no measured claim about 2.0 versus 1.0.
- The answerability threshold `tau` is the placeholder 0.0 and rejects nothing. Profile choice and `tau` are not tuned (task T17 is partial).
- In the preliminary run, lexical-only retrieval returned almost nothing for natural-language questions because `websearch_to_tsquery` ANDs every term; hybrid results therefore equal dense results. Not yet fixed.
- The CI retrieval gate is not armed.
- FORCE RLS is deferred, no TypeScript caller uses `scoped()`, and Neon's default owner role probably has `BYPASSRLS`. Real database-enforced isolation needs the app to connect as `scribe_app`.
- `/admin/analytics` (instance-wide counts) is restricted to users whose Clerk session token carries `metadata.role = "admin"`; everyone else gets a 404. Until that claim is configured in the Clerk dashboard (user public metadata `{"role": "admin"}` plus `{"metadata": "{{user.public_metadata}}"}` in the session token claims) nobody can open the page.
- Source versions and claim verification require the Python engine; the legacy engine does neither.
- The 100,000-chunk performance step was not measured. No rate limiting on `/api/py/*`; a single static internal secret ([`threat-model.md`](docs/scribe2/threat-model.md) section 6).
- Only the active version of a source is searched. No shared workspaces, roles or OpenTelemetry (ADR-0012).
- Roughly 100 UI files under `components/ai-elements/` and `components/ui/` are not reachable from any route (found by a static import walk; not removed).

## Security

Workspace scoping on every query, 60-second audience-bound internal tokens, document text treated as untrusted data (fixed evidence markers, a single server-scoped tool, labels issued by the server), no document text in logs, and a signature-verified Razorpay webhook. An adversarial test suite and a route-by-route IDOR sweep back these up. This is an engineering review with tests, not a penetration test. See [`docs/scribe2/threat-model.md`](docs/scribe2/threat-model.md).

## Documentation

[`docs/README.md`](docs/README.md) indexes everything. Start with [`ARCHITECTURE.md`](docs/scribe2/ARCHITECTURE.md), then [`DESIGN.md`](docs/scribe2/DESIGN.md). [`CONTRIBUTING.md`](CONTRIBUTING.md) covers how to work on the repo, and [`HANDOVER.md`](docs/scribe2/HANDOVER.md) is the running status log.

## Repository layout

```
app/                  Next.js routes: chat, upload, files/[id], compare, eval, billing, api/*
components/, lib/     UI components; server helpers (workspace, internal token, evidence, ingestion, billing)
proxy.ts              Clerk middleware and the public-route list
api/py/index.py       Vercel entry for the Python service (rewrite in vercel.json)
intelligence/         Python package: scribe_intel (service), scribe_ingest, scribe_eval, scribe_perf, tests
drizzle/              Migrations 0000-0019, rollback/, deferred/
benchmarks/           golden-v1 dataset, gate/ (baseline and cache absent), perf/ results
fixtures/             Northwind corpus (24 docs), adversarial documents, distractor generators
scripts/              Migration helpers, eval baseline, legacy backfill, demo seed, diagram generator
docs/                 adr/, scribe2/ (design, architecture, handover, threat model, benchmarks), assets/
```

## License

MIT. See [`LICENSE`](LICENSE).

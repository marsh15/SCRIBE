# Scribe 2.0 architecture

How the system is built **as of branch `scribe2/m0-foundation` (HEAD `63fa5bf` plus the T33 files)**. Decisions and invariants live in `DESIGN.md` (cited here as "Design §"); this file describes the code that exists. Anything not run against a real deployment or real provider is marked **not verified**.

## 1. Components

| Component | Where | Role |
|---|---|---|
| Next.js app (Node) | `app/`, `components/`, `lib/`, `proxy.ts` | Clerk auth, chat streaming (AI SDK), Source intake and upload (Vercel Blob), billing, UI (chat, sources, source detail, compare lab, eval dashboard) |
| Python intelligence service | `intelligence/src/scribe_intel/`, entry `api/py/index.py`, rewrite `/api/py/*` in `vercel.json` | FastAPI: `/health`, `/retrieve`, `/compare`, `/evidence/open`, `/verify`, `/verify/read`, `/ingest/source`, `/ingest/step`, `/ingest/sweep`, `/ingest/purge` |
| Ingestion library | `intelligence/src/scribe_ingest/` | Parsers (PDF, Markdown, TXT, DOCX, CSV), chunker, Gemini embedder and OCR client, indexer with the activation transaction, purge, legacy upgrade |
| Evaluation CLI | `intelligence/src/scribe_eval/` (`scribe-eval`) | Dataset validation, metrics, scoring, answer scoring, run compare/publish, regression gate, corpus ingest |
| Performance CLI | `intelligence/src/scribe_perf/` (`scribe-perf`) | Synthetic scaling harness for dense and lexical search |
| Postgres | `lib/db-schema.ts` plus hand-written migrations in `drizzle/` | Single store: relational data, pgvector, Postgres FTS, RLS policies, queue functions |
| Object storage | Vercel Blob (private) | Original bytes of each Source version |
| Providers | Gemini (embeddings, OCR, verifier, chat), Voyage (rerank) | Behind adapters with cache and fakes for tests |

Internal auth: Next.js mints a 60-second HS256 token (`lib/internal-token.ts`: `sub`, `wid`, `scope`, `aud=scribe-intel`); Python takes the workspace only from the token or the job row (INV-15). The browser never sees the token.

## 2. Runtime topology

```
Browser ─► Next.js (Vercel, Node) ─────────────► Neon Postgres (pgvector, RLS)
           Clerk · chat stream · uploads          ▲  intended role scribe_app (NOBYPASSRLS)
           mints 60 s internal token              │  owner role: migrations
             │ /api/py/*                          │
             ▼                                    │
           Python FastAPI (Vercel Python fn) ─────┘
           /retrieve /compare /verify /ingest/*
             │                     │
             ▼                     ▼
           Vercel Blob (private)   Gemini · Voyage
```

Eval, perf and the regression gate run from the CLI (locally or GitHub Actions), never on Vercel. **Not verified:** that Python co-deploys with Next.js on Vercel Hobby (checklist `T02-verification.md`); nothing here has run on Vercel or Neon.

## 3. Data model (summary of Design §B2)

- **Tenancy:** `workspaces` (kind `personal` or `eval`), `workspace_members`. `workspace_id` on files, chats, traces and every index table.
- **Sources and versions:** `files` is the Source table (soft delete via `deleted_at`, `desired_version_id`, `active_version_id`). `source_versions` is immutable once stored (trigger), unique `(source_id, version_number)`, `duplicate_of` for identical bytes.
- **Profiles (immutable):** `embedding_profiles`, `chunk_profiles`, `index_profiles`, `retrieval_profiles` (config + hash).
- **Index:** `index_generations` (BUILDING, READY, ACTIVE, SUPERSEDED, FAILED, CANCELLED), `active_indexes` (one active generation per source and index profile), `document_blocks` (canonical text with page, bbox, row, offsets), `chunks` (parent/child, generated `fts_en`/`fts_simple`), `chunk_source_spans`, `chunk_embeddings_1536` (HNSW) and `chunk_embeddings_3072` (legacy, exact search only).
- **Queue:** `ingestion_jobs` with lease token, attempts, error code, next retry; OCR page results in `source_version_ocr_pages`.
- **Traces and evidence:** `retrieval_traces`, `retrieval_candidates`, `evidence_bundles`, `evidence_items`, `claim_verifications`.
- **Evaluation:** `eval_runs`, `eval_results` (global, read by the `/eval` dashboard for users with an eval workspace).
- Billing tables stay keyed by user.

Migrations: `drizzle/0000`-`0018`. Only `0010` onward are replayable on an empty database; `scripts/apply-migrations.sh` builds a schema from `lib/db-schema.ts` via `drizzle-kit export` and replays `0010+` (ADR-0003). Deferred, guarded, manual SQL: `drizzle/deferred/force_rls.sql`, `drizzle/deferred/retire_legacy.sql`.

## 4. Ingestion step machine (Design §B3, ADR-0010, ADR-0011)

1. Upload: browser uploads straight to Vercel Blob; `/api/sources/reserve` and `/api/sources/upload-complete` create the Source, a `source_versions` row and an `ingestion_jobs` row (identical bytes become `duplicate_of`).
2. A tick (`/api/internal/ingest/tick`, re-fired through `after()` while work remains; "process now" button; daily cron `run` and `sweep`) calls Python `/ingest/step`.
3. Each step claims one job through a `SECURITY DEFINER` function with `FOR UPDATE SKIP LOCKED` and a fresh lease token. All progress is written `WHERE lease_token = :mine`; zero rows means the lease was lost and nothing more is written (INV-5).
4. Stages: PARSE, OCR (only pages with almost no text; bounded pages per step; OCR blocks flagged), CHUNK (children up to 384 tokens inside section parents, `o200k_base` as a recorded proxy), EMBED (batched, resumable from the embedded count), ACTIVATE.
5. ACTIVATE is one transaction: lock the source, check it is not deleted and this version is still desired, check the generation is READY with all chunks embedded, upsert `active_indexes`, supersede the old generation, delete its embeddings (INV-4).
6. Errors: retryable (rate limit, transient provider, database or storage) back off up to 5 attempts; permanent (invalid document, unsupported, resource limit, empty extraction) fail at once with a plain message. Limits: 300 pages, 50 OCR pages, 5,000 CSV rows per source (D21).
7. Delete is a soft delete that removes the Source from retrieval in the same transaction (INV-14); a background purge removes blobs and rows later.
8. `INGEST_ENGINE=python|legacy` selects the engine; the default is `legacy`.

## 5. Query path (Design §B4)

`POST /api/py/retrieve` → one `REPEATABLE READ READ ONLY` transaction with `set_config('app.workspace_id', …, true)` → query embedding (query task format) while lexical search runs → dense top 40 (exact or HNSW; `auto` switches at 20,000 chunks, `AUTO_ANN_THRESHOLD`) and Postgres FTS (`fts_en`, `fts_simple`) → RRF (k=60, ranks only, INV-10) → Voyage rerank top 20 (on failure: fused order and `degraded: rerank_unavailable`) → dedup by content hash and parent → parent expansion and a 6,000-token packer → answerability gate → trace, candidates and evidence bundle persisted.

Retrieval profiles seeded by `0012`: `dense-only`, `lexical-only`, `hybrid-rrf`, `dense-rerank`, `hybrid-rrf-rerank` (the five "profiles A-E"). Strategies: `dense-exact`, `dense-ann`, `lexical-v1`, `legacy-dense`. `POST /compare` runs 2-5 profiles in one snapshot and reports rank movement.

Chat: `RETRIEVAL_ENGINE=python|legacy` (default `legacy`, no silent fallback). In `python` mode the model has one tool (retrieve) with server-fixed scope and a 3-step cap, evidence is wrapped between `BEGIN/END_EVIDENCE_DATA` markers (INV-13), and Supermemory is off (D16).

## 6. Evidence and verification flow

1. The retriever issues labels `[E1]…` in order, unique per turn; the bundle stores quote, quote hash, version and page for each item.
2. The model cites labels. After streaming, `lib/chat-verify-stream.ts` splits claims (`lib/evidence/claims.ts`) and calls `/verify`.
3. `/verify` runs deterministic checks (label exists in this turn's bundle, version, quote hash/span) and, for cited claims, one bounded Gemini Flash call (at most 20 claims, 20 s timeout; more, timeout, provider error or no key give `UNVERIFIED`).
4. Statuses: `VERIFIED` (only when deterministic checks pass AND the verdict is `SUPPORTED`, INV-9), `PARTIAL`, `UNSUPPORTED`, `UNCITED`, `INVALID_CITATION`, `UNVERIFIED`. The verifier sees the stored quote only, not the expanded parent text.
5. Clicking a citation calls `/api/evidence/[id]` → `/evidence/open`, which re-checks workspace and deletion and returns the quote, page and bbox or row; the UI highlights it ("page unavailable" when unknown, "source deleted" when purged).
6. The answerability gate uses the reranker top score against `tau`. **`tau` is the uncalibrated placeholder 0.0 (`tau_calibrated=False`), so the gate rejects nothing today** (calibration was task T17, which has not been done).

## 7. Tenancy and RLS: current state

- Migration `0013` enables RLS (not FORCEd) on workspace tables, creates `scribe_app` (NOBYPASSRLS) and `scribe_definer`, and the `app_*` / `scribe_*` SECURITY DEFINER functions.
- Every query also filters by `workspace_id` explicitly, and Python sets `app.workspace_id` per transaction. Cross-tenant behaviour is tested in SQL and for every route (`__tests__/security/`, `intelligence/tests/security/`).
- **FORCE RLS is deferred** (`drizzle/deferred/force_rls.sql`): the owner role still bypasses policies, so isolation for the owner role rests on application filters. No TypeScript caller uses `scoped()` yet. Rollout order: apply 0013, move callers to `scoped()` and a `scribe_app` DSN (enable LOGIN out of band), only then run the deferred script. See `threat-model.md` R1, R2 and `HANDOVER.md` §8. **Nothing has been run against Neon.**

## 8. Deployment topology

One Vercel Hobby project (300 s functions, 500 MB Python bundle, once-daily cron, non-commercial so `FEATURE_BILLING_ENABLED` stays off); Neon Postgres; Vercel Blob. `vercel.json` has the two crons, the `/api/py/*` rewrite and the Python function config (`maxDuration` 300). Python dependencies for Vercel are the root `requirements.txt`, kept in sync by hand with `intelligence/pyproject.toml`. If co-deployment fails, ADR-0002 prescribes a second Vercel project rooted at `intelligence/`. **Not verified on Vercel or Neon.** Local/CI: `docker-compose.yml` (pgvector pg17 on port 54329) and `.github/workflows/ci.yml` (jobs `node`, `python`, `retrieval-gate`; the CI jobs themselves have not run on GitHub).

## 9. ADR index (`docs/adr/`)

| ADR | Title |
|---|---|
| 0001 | Private Blob uploads with server-owned Source intake |
| 0002 | Python service runs inside the existing Vercel deployment |
| 0003 | Drizzle is the only migration owner |
| 0004 | Light parsers plus Gemini OCR |
| 0005 | Lexical search is Postgres FTS, not BM25 |
| 0006 | Reciprocal rank fusion plus Voyage rerank |
| 0007 | Personal workspaces enforced with Postgres RLS |
| 0008 | Evidence labels and claim verification |
| 0009 | Evaluation labels are independent of any index profile |
| 0010 | Ingestion as leased, resumable steps |
| 0011 | Source versions and index generations |
| 0012 | Deferred to P1/P2 |

## 10. Limits and known gaps

- Hobby limits drive the design (Design §A2): resumable steps of at most 300 s, evaluation outside Vercel.
- Only active versions are searched (D22); no shared workspaces or roles; no OpenTelemetry; lexical search is Postgres FTS, not BM25 (ADR-0012).
- Retrieval profile choice and `tau` are not tuned (T17 not done). Which profile is best is **not measured**; see `benchmarks/final.md` (placeholder).
- The golden set (200 cases, 80 machine-drafted and unreviewed) has not been human-reviewed; the retrieval gate has no real cache or baseline and is **not armed** (`release-rules.md`).
- Scanned-PDF OCR, claim verification and reranking have only run against fakes or mock transports; no live Gemini or Voyage call has been made by any test.
- Performance numbers are synthetic, one Mac; 100,000 chunks was not measured (`benchmarks/performance.md`).
- Security: engineering review with tests, not a penetration test; 15 open or accepted residual risks in `threat-model.md` §6.

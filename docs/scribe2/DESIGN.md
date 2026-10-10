# Scribe 2.0 design

Source of truth for every Scribe 2.0 task (T01–T33). Tasks cite sections of this file as "Design §". If code and this file disagree, fix one of them in the same PR.

## Part A — Decisions

### A1. Product decisions

| Topic | Decision |
|---|---|
| Name | Scribe 2.0, same repo and deployment |
| Hosting | Existing Vercel project, Hobby plan. Python runs as Vercel Python functions |
| Parsing | Lightweight parsers plus Gemini OCR. Docling is dropped |
| Reranker | Voyage rerank-2.5 behind a replaceable adapter |
| Existing data | Preserved. Every existing file is backfilled as Source version 1 |
| Tenancy | One personal workspace per user, enforced with Postgres RLS. No invites or roles UI in P0 |
| Delivery | 4 milestones over about 14 weeks. M1 (about week 5) is presentable on its own |

### A2. Constraints that follow

- Hobby limits: functions run at most 300 s with 2 GB memory; Python bundles up to 500 MB; about 4 CPU-hours per month; cron runs at most once a day. Therefore ingestion runs as resumable steps of 300 s or less that re-trigger themselves, and the daily cron is only a safety sweep.
- Evaluation runs and performance tests run from a CLI (locally or in GitHub Actions), never on Vercel. The 100k-chunk performance test runs on local Docker Postgres because Neon's free 0.5 GB cannot hold it.
- Hobby is non-commercial only, so `FEATURE_BILLING_ENABLED` stays off in production. Usage metering still runs because it powers cost tracking.
- The `drizzle-orm/neon-http` driver cannot hold a transaction open. TS sets workspace context inside each batch transaction with `set_config('app.workspace_id', …, true)`. Python uses psycopg 3 with real transactions.
- `gemini-embedding-2` is stable and normalizes reduced-dimension output automatically; code still re-normalizes defensively. `gemini-embedding-2-preview` (3072) and `gemini-embedding-2` (1536) vectors are treated as incompatible spaces unless T12 measures otherwise.

### A3. Engineering decisions

| # | Decision | Why |
|---|---|---|
| D1 | Python owns retrieval at query time; Next.js calls it on every chat turn | Chat and evaluation must run the same code |
| D2 | Next.js keeps Clerk, chat streaming (AI SDK), uploads, billing | Already works; rewriting adds nothing |
| D3 | Python package in `intelligence/`, thin `api/py/index.py` entry, reached through a `/api/py/*` rewrite. If co-deploying fails, move to a second Vercel project rooted at `intelligence/` | One deployment; T02 tests this first |
| D4 | Python uses raw SQL through psycopg 3. Drizzle stays the only migration owner; Postgres-specific DDL goes in custom Drizzle SQL migrations | One migration history; a schema-contract test catches drift |
| D5 | Internal auth: Next.js mints a 60-second HS256 token with `sub`, `wid`, `scope`, `aud=scribe-intel`. Python never reads the workspace from a request body | The browser never sees the token |
| D6 | Parsers: pdfplumber (PDF text, font sizes, boxes, tables), python-docx, stdlib `csv` with dialect sniffing. Low-text PDF pages are rendered with pypdfium2 and transcribed by Gemini 2.5 Flash. PyMuPDF excluded (AGPL) | Fits the bundle, permissive licenses |
| D7 | Canonical text for a version is its `document_blocks` rows. Spans and quote checks use it. No separate parsed-artifact file | One source of truth |
| D8 | Token counting uses tiktoken `o200k_base` as a recorded proxy, stored in the chunk profile | No local Gemini tokenizer; API per chunk is too slow |
| D9 | Child chunks ≤ 384 tokens following block boundaries; 48-token overlap only when a single block must be split; parent chunk ≈ 1,536 tokens at section level; heading path is embedded but stored separately | Starting values, tuned on dev split |
| D10 | Lexical search: `fts_en` (English stemming, heading weight A, body B) and `fts_simple` (no stemming, for identifiers). `websearch_to_tsquery` on both; quoted phrases use phrase matching. Called "Postgres FTS", never "BM25". `pg_trgm` only if identifiers measure poorly | Honest naming |
| D11 | Dense: `vector(1536)` + HNSW (m=16, ef_construction=64); query-time `hnsw.ef_search=100`, `iterative_scan=relaxed_order` (pgvector ≥ 0.8). `dense_mode: exact \| ann \| auto`; auto = exact below 20k chunks per workspace. T32 measures | Personal workspaces are small |
| D12 | Snapshot pinning: each retrieval runs in one `REPEATABLE READ READ ONLY` transaction | Dense and lexical see the same active versions |
| D13 | Answerability gate uses the reranker's top score against τ calibrated on dev/validation, stored in the profile; never cosine similarity. States: `ANSWERABLE`, `INSUFFICIENT_EVIDENCE`, `RETRIEVAL_FAILED`. `CONFLICTING_EVIDENCE` is model-flagged in P0, detected in P1 | Calibrated, not guessed |
| D14 | The model cites `[E1]`-style labels from the evidence bundle; labels are unique across all searches in one turn. After streaming, a verification step sends states through `createUIMessageStream`. The fake "No source links were generated" fallback in `ensureStructuredAnswer` is deleted | Honest verification states |
| D15 | Semantic verifier: one Gemini Flash call, JSON schema, up to 20 claims. Beyond that, or on a 20 s timeout, claims are `UNVERIFIED`. Prompt version is stored | Bounded cost and latency |
| D16 | Supermemory is off whenever evidence mode is on | Injected memory cannot be cited |
| D17 | Eval labels are profile-independent: `(doc, version, quote, page, grade)`. A retrieved chunk is relevant if its normalized text contains the normalized quote | One label set for legacy, new, and future parsers |
| D18 | Embedding and rerank responses cached by `(model, dims, task, sha256(text))` | Cheap, deterministic repeat runs and CI |
| D19 | Observability: stage timings and costs go to database trace tables and structured logs carrying `trace_id`. OpenTelemetry is P1 | Hobby has no log drains |
| D20 | A superseded generation keeps chunks and spans (historical citations resolve) but its embeddings are deleted. Legacy 3072-dim embeddings are kept only in the eval workspace. halfvec is a measured experiment | Neon free-tier storage |
| D21 | Limits: 300 pages/source, 50 OCR pages/source, 5,000 indexed CSV rows. Over limit → `RESOURCE_LIMIT` with a plain message | Bounded Hobby CPU and cost |
| D22 | P0 searches active versions only. "2024 vs 2026" questions use separate sources. Searching old versions is P1 | Simple activation rules |

### A4. Departures from the rough plan

- Docling and OCR worker replaced by D6.
- No Python worker container; ingestion runs as Vercel step functions (T18).
- OpenTelemetry moves to P1.
- Evaluation runs from a CLI and CI; the UI reads results and runs single-query comparisons.
- `documents` migrates into the new chunk schema as a legacy generation, so there ends up being one retrieval schema.
- `lib/ingestion/worker.ts` and `/api/uploads/complete` are removed (T04).

## Part B — Shared design

### B1. Runtime topology

```
Browser ─► Next.js (Vercel, Node) ─────────────► Neon Postgres (pgvector, RLS)
           Clerk · chat stream · uploads          ▲  role scribe_app (NOBYPASSRLS)
           BFF mints 60 s internal token          │  owner role: migrations only
             │ /api/py/* (same deployment)        │
             ▼                                    │
           Python FastAPI (Vercel Python fn) ─────┘
           /retrieve /compare /verify /ingest/step
             │                     │
             ▼                     ▼
           Vercel Blob (private)   Gemini (embed, OCR, verifier) · Voyage (rerank)
```

`/ingest/step` triggers: upload-complete callback → `after()` tick; a tick re-fires itself while claimable work remains; user "process now"; daily cron sweep. Eval and perf run via `uv run scribe-eval` / `scribe-perf` in GitHub Actions.

### B2. Data model

All new keys are UUIDs.

- `workspaces(id, kind personal|eval, owner_user_id, created_at)`, `workspace_members(workspace_id, user_id, role='owner')`.
- `files` remains the Source table. Gains `workspace_id`, `deleted_at`, `desired_version_id`, `active_version_id`.
- `chats`, `rag_traces` gain `workspace_id`. Billing tables stay keyed by user.
- `source_versions(id, source_id, workspace_id, version_number, storage_key, sha256, size_bytes, mime_type, upload_state, duplicate_of, created_by, created_at)`, `UNIQUE(source_id, version_number)`. Immutable once stored.
- `embedding_profiles`, `chunk_profiles`, `index_profiles(parser_profile, chunk_profile_id, embedding_profile_id)`, `retrieval_profiles(id, name, config jsonb, config_hash)`. Immutable; a change creates a new row.
- `index_generations(id, source_version_id, index_profile_id, workspace_id, status BUILDING|READY|ACTIVE|SUPERSEDED|FAILED|CANCELLED, expected_chunks, embedded_chunks, error_code, timestamps)`.
- `active_indexes(source_id, index_profile_id, generation_id)`, PK `(source_id, index_profile_id)`.
- `document_blocks(id, source_version_id, workspace_id, block_index, type, text, heading_path text[], page, bbox real[4], ocr bool, row_number, char_start, char_end, meta)`, `UNIQUE(source_version_id, block_index)`.
- `chunks(id, generation_id, source_version_id, workspace_id, parent_id, chunk_index, content, heading_text, content_hash, token_count, fts_en, fts_simple)`.
- `chunk_source_spans(chunk_id, block_id, char_start, char_end, page, bbox)`.
- `chunk_embeddings_1536(chunk_id, workspace_id, embedding_profile_id, embedding vector(1536))` and `chunk_embeddings_3072(…)` (legacy, exact search only).
- `ingestion_jobs` extended with `source_version_id, generation_id, lease_token, lease_expires_at, attempts, error_code, last_error, next_retry_at`.
- `retrieval_traces(id, workspace_id, chat_id, message_id, query, profile_id, config_hash, status, degraded, stage_ms jsonb, counts jsonb, cost jsonb, created_at)` and `retrieval_candidates(trace_id, chunk_id, dense_rank, dense_score, lexical_rank, lexical_score, fusion_rank, rerank_rank, rerank_score, selected)`.
- `evidence_bundles(id, trace_id, workspace_id, answerability, top_rerank_score)` and `evidence_items(id, bundle_id, label, chunk_id, source_version_id, quote, quote_hash, page_start, page_end, heading_path)`.
- `claim_verifications(id, message_id, bundle_ids, claim_text, labels[], deterministic_ok, status, verifier_model, prompt_version)`.
- `eval_runs(id, dataset_version, split, profile_id, config_hash, code_commit, started_at, finished_at)` and `eval_results(run_id, case_id, metrics jsonb, ranking jsonb, latency_ms, cost)`.

### B3. Ingestion step machine

`PARSE → CHUNK → EMBED (batched, repeatable) → ACTIVATE`.

Each invocation claims one job with `FOR UPDATE SKIP LOCKED` inside a `SECURITY DEFINER` claim function that sets a fresh `lease_token` and a lease of now + 330 s. The step does work, then writes progress with `WHERE lease_token = :mine`. Zero rows updated means the lease was lost: stop and write nothing. Each step first checks that its version is still the desired one and the source is not deleted; otherwise the job becomes `SUPERSEDED` or `CANCELLED`.

`ACTIVATE` is a single transaction:
1. Lock the source row.
2. Check `deleted_at IS NULL` and `desired_version_id` matches this version.
3. Check the generation is `READY` with `embedded = expected`.
4. Upsert `active_indexes`, mark the old generation `SUPERSEDED`, set `active_version_id` and `status='ready'`.
5. Delete the old generation's embeddings (D20).

Retry taxonomy: `RATE_LIMITED`, `TRANSIENT_PROVIDER`, `DATABASE_UNAVAILABLE`, `STORAGE_UNAVAILABLE` retry with backoff `2^n` minutes plus jitter, up to 5 attempts. `INVALID_DOCUMENT`, `UNSUPPORTED_FORMAT`, `RESOURCE_LIMIT`, `EMPTY_EXTRACTION` are permanent. Unknown failures get 2 attempts.

### B4. Query path

1. Next.js authenticates, resolves the workspace, calls `POST /api/py/retrieve {query, profile?, source_ids?}`.
2. Python opens a `REPEATABLE READ` transaction with `set_config('app.workspace_id', …, true)`.
3. It embeds the query while the lexical query runs.
4. Dense search takes the top 40.
5. RRF (k=60) fuses dense and lexical lists.
6. Voyage reranks the top 20.
7. Duplicates removed by `content_hash` and parent.
8. Parents expanded; context packed into a 6,000-token budget.
9. Answerability gate runs.
10. Bundle, trace, and candidates persisted; bundle returned.

If the reranker fails, fused order is used and the result is `degraded: "rerank_unavailable"`. If the database or embedding call fails, the result is `RETRIEVAL_FAILED`. The legacy TS path is available only through `RETRIEVAL_ENGINE=legacy`; there is no silent fallback.

### B5. Invariants

- **INV-1** Every read or write of workspace data runs with a server-derived workspace context; no context returns zero rows (policies compare against `NULLIF(current_setting('app.workspace_id', true), '')::uuid`).
- **INV-2** Workspace, deletion, and active-generation filters sit inside each SQL query before `LIMIT`.
- **INV-3** Retrieval reads only chunks from generations listed in `active_indexes` for the profile's index profile, and only from sources that are not deleted.
- **INV-4** At most one active generation per `(source, index profile)`, switched in one transaction.
- **INV-5** Only the current lease holder can record progress or activate.
- **INV-6** A stored source version never changes.
- **INV-7** Citation locations come from source spans. An estimated page is never shown as exact; when unknown the UI says "page unavailable".
- **INV-8** An evidence item can be read only by its own workspace, and only while its source is not deleted.
- **INV-9** "Verified" is shown only when deterministic checks pass and the verifier returns `SUPPORTED`.
- **INV-10** Scores from different rankers are never added together. Fusion uses ranks only.
- **INV-11** Every eval result records dataset version, profile hash, commit, model IDs, and prompt versions.
- **INV-12** The test split is used only for milestone reports, never for tuning.
- **INV-13** Document text is untrusted. It cannot change tools, scope, or system instructions, and private text never goes into logs.
- **INV-14** Deleting a source removes it from retrieval in the same transaction it is deleted.
- **INV-15** Python accepts only internal tokens minted by Next.js; the workspace comes only from the token or the job row.
- **INV-16** Degraded results are labelled degraded in both the response and the UI.

### B6. Edge cases and owning tasks

| Case | Expected behavior | Task |
|---|---|---|
| Blob callback delivered twice | One version, one job | T19 |
| Worker step times out mid-embed | Lease expires; next claim resumes from embedded count | T18 |
| Old worker finishes late | Its writes match zero rows and are discarded | T18 |
| v3 uploaded while v2 building | v2 cancelled at next step boundary; v3 activates | T19 |
| New version fails | Previous active version stays searchable | T19 |
| Same bytes uploaded again | Marked `duplicate_of`; no new generation | T19 |
| Source deleted during indexing | Gone from search immediately; activation refused; purged later | T20 |
| Scanned or mixed PDF | Only low-text pages OCR'd; OCR blocks flagged; page known, bbox null | T21 |
| Encrypted or corrupt PDF | Permanent `INVALID_DOCUMENT` with plain message | T09 |
| Empty extraction | `EMPTY_EXTRACTION`, not stuck processing | T09 |
| CSV with BOM, `;` delimiter, ragged rows, no header | Encoding fallback, dialect sniffing, generated column names, row provenance | T10 |
| Repeated page headers/footers | Marked page furniture, excluded from chunks | T09 |
| Two identical paragraphs | Separate chunks with separate spans | T11 |
| Identifier query such as `ERR_AUTH_403` | `fts_simple` matches exactly | T13 |
| Empty workspace | `INSUFFICIENT_EVIDENCE` with a "no sources" message | T25 |
| Reranker 429 or down | Fused order used, marked degraded | T14 |
| Model cites `[E9]` not in bundle | Deterministic check marks invalid | T26 |
| Answer sentence has no citation | Marked "uncited" | T26 |
| Document says "ignore instructions…" | No effect; covered by adversarial tests | T27 |
| Another user's trace/evidence/blob ID in a URL | 404 | T15, T27 |
| Source deleted after an answer cites it | Citation shows "source deleted" | T20 |
| Version activates while a query runs | Query sees one consistent snapshot | T13 |
| Preview deployment protection blocks internal fetch | Use bypass header from `VERCEL_AUTOMATION_BYPASS_SECRET` | T02 |
| Neon pooler and prepared statements | `prepare_threshold=None` | T02 |
| Free-tier rate limits for Voyage or Gemini during evals | Response cache; add a payment method to Voyage to lift RPM | T06 |

## Part C — Schedule and risks

| Weeks | Milestone | Tasks |
|---|---|---|
| 1 | M0 Foundation | T01–T04 |
| 2–5 | M1 Measured hybrid retrieval | T05–T17 |
| 6–8 | M2 Versioned ingestion, exact provenance | T18–T24 |
| 9–11 | M3 Evidence, verification, security | T25–T28 |
| 12–14 | M4 Lab, performance, release | T29–T33 |

Risks: (1) Python/Next.js may not co-deploy — T02 tests first, fallback is a second Vercel project. (2) Hobby CPU hours — evals run in CI; lower OCR cap if needed. (3) Voyage/Gemini free-tier limits — payment method, caching, concurrency limits. (4) Neon 0.5 GB — D20 and dropping legacy 3072 embeddings (T22). (5) Labeling time for T05/T28 needs human review.

Deferred to P1/P2 (ADR-0012): query decomposition, automatic contradiction detection, reusing embeddings for unchanged chunks, searching old versions, shared workspaces and roles, OpenTelemetry, pg_trgm/true BM25, Docling as offline comparison, graph and multimodal retrieval, MCP tools.

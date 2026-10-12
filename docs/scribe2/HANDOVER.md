# Scribe 2.0 handover

This file has what a new engineer (or a new Claude session) needs to continue the work. Read it first, then `docs/scribe2/DESIGN.md` (the source of truth for decisions, data model and invariants) and the ADRs in `docs/adr/0002-0012`.

Repo: `github.com/marsh15/SCRIBE` · live app: https://scribe-marsh.vercel.app/ · working branch: `scribe2/m0-foundation` (branched from `codex/production-readiness` @ `62ccb1a`; upstream `main` = `0edddb1`). Scribe 2.0 has not been run against Neon or the real Voyage API; Gemini has been used locally for the baseline and M1 preliminary runs. Main was deployed once by mistake and rolled back (section 11).

## 1. What Scribe 2.0 is

Scribe 2.0 upgrades the existing Scribe RAG app (Next.js 16, Clerk, Drizzle, Neon Postgres with pgvector, Vercel Blob, AI SDK, Gemini) into a measured retrieval system with verifiable citations. It consists of:

- Hybrid retrieval (dense plus Postgres FTS, fused by RRF, reranked by Voyage rerank-2.5), owned by a Python service.
- Versioned sources and index generations with atomic activation, replacing delete-then-reindex.
- Exact citations (page, bbox, row) from source spans, evidence labels such as `[E1]`, and claim verification after the stream.
- Personal workspaces enforced by Postgres RLS.
- A labeled evaluation set, a metrics CLI and a CI regression gate, so every claim of improvement is measured.

Hard constraints (user decisions): the project stays on **Vercel Hobby** (300 s functions, 500 MB Python bundle, once-daily cron, non-commercial, so `FEATURE_BILLING_ENABLED` stays off) with one deployment. Parsing uses light parsers plus Gemini OCR, with no Docling. The Voyage reranker sits behind an adapter. Existing production data is preserved and backfilled as version 1. The plan runs 14 weeks in milestones.

## 2. What has been done (15 commits on `scribe2/m0-foundation`)

| Task | Commit | Result |
|---|---|---|
| T01 | `e137ba6` | `docs/scribe2/DESIGN.md` (decisions D1-D22, data model, ingestion step machine, query path, invariants INV-1-16, edge-case table), ADRs 0002-0012, `BASELINE_1.0.md` (current behaviour with file:line refs), glossary additions in `CONTEXT.md`. |
| T02 | `a1060f6` | FastAPI service in `intelligence/` (uv, Python 3.12), thin `api/py/index.py` entry, `/api/py/*` rewrite in `vercel.json`, 60 s HS256 internal tokens (`lib/internal-token.ts`, `lib/intelligence-client.ts`, secret `INTERNAL_SERVICE_SECRET`), `docker-compose.yml` (pgvector pg17, port 54329), `.github/workflows/ci.yml`, `scripts/apply-migrations.sh`, `docs/scribe2/T02-verification.md` (**preview-deploy checklist, not yet run**). |
| T03 | `935b84f` | `workspaces` / `workspace_members`, `workspace_id` on files/chats/rag_traces, migration `0008` with backfill, `lib/workspace.ts` (`ensurePersonalWorkspace`, `resolveWorkspace`), all TS queries scoped. `/api/chat` now 404s on a chat id the caller does not own. |
| T04 | `1c20438` | Legacy ingestion removed: `lib/ingestion/worker.ts`, `batch.ts`, old scripts deleted; `/api/uploads/{sign,complete}` and `/api/ingest/batch*` return 410; migration `0009` marks stranded legacy sources failed. lint/vitest now ignore `.claude/`. |
| T05 | `9b82680` | Eval corpus `fixtures/corpus/` (24 Northwind Logistics docs, 5 formats + scanned PDF with `.truth.txt`, `FACTS.md`, built by `fixtures/build_corpus.py`), `benchmarks/datasets/golden-v1/` (120 cases, 10 categories × 12, 40/20/40 dev/validation/test split, seed 20260101), `scribe-eval validate` and `assign-splits`. |
| T06 | `35422f0` | `scribe_eval`: metrics (recall/hit@k, MRR, nDCG@10, P@5, CIs), paired bootstrap, run records (INV-11), response cache with replay mode (D18), `scribe-eval score` / `compare`; `--split test` refused without `--milestone-report`. |
| T07 (harness only) | `2ee1051` | `scripts/eval-baseline.ts`: ingests the corpus with the *current* TS pipeline into an eval workspace and writes rankings for threshold 0.3 and no-threshold. **Live run not done** (needs key + DB). |
| T08 | `a5f4183` | Migrations `0010` (all B2 tables, FTS generated columns, HNSW on 1536, immutability triggers) and `0011` (seed profiles), `scripts/backfill-legacy-generations.ts`, Python schema tests, `T08-notes.md`. |
| T09 | `d1ad4c2` | `scribe_ingest.parse(bytes, mime)` for PDF/Markdown/TXT: typed blocks, heading paths, exact pages, bbox, furniture detection, canonical-text spans, OCR-candidate pages, `download_private_blob`. |
| T10 | `2ff0383` | DOCX (document order, headings, header-first tables) and CSV (BOM, delimiter sniffing, ragged rows, 5,000-row limit, raw record kept in `meta["raw"]`). |
| n/a | `6bce48e` | D17 refinement: CSV evidence quotes (raw lines) match a chunk when all non-empty cells appear in order. |
| T11 | `312293c` | `chunk_document`: ≤384-token children inside section parents, table headers repeated, block-relative spans, vendored `o200k_base` vocabulary (no network), Python port of the legacy 4000/400 chunker verified against TS on 27 fixtures. |
| T13 | see git log | `retrieval/{dense,lexical,search,queries,registry}.py`, `POST /retrieve` (strategies `dense-exact`, `dense-ann`, `lexical-v1`, `legacy-dense`), snapshot isolation, scoping tests; see `T13-notes.md`. |
| T14 | see git log | RRF, Voyage rerank adapter, dedup, packing, profiles A-E, `pipeline.py`, hybrid handlers served by `POST /retrieve`, parent loader SQL, migration `0012` (seeds profiles, verifies stored config/hash), DB-backed `upsert_profiles` tests, test-data cleanup fixtures; see `T14-notes.md`. **Not done:** live Voyage run (needs `VOYAGE_API_KEY`). |
| T26 | uncommitted | `POST /verify` + `/verify/read` (scope `verify`), `scribe_intel/verification.py` + `verify_routes.py`, `lib/evidence/{claims,verification}.ts`, `lib/chat-verify-stream.ts`, `components/claim-badge.tsx`; no migration (B2 table already existed). See section 9. |
| T12 | `1a39fb6` | `GeminiEmbedder` (gemini-embedding-2, 1536 dims, text-prefix task format), staged idempotent indexer with the B3 activation transaction, `POST /ingest/source`, `scribe-eval ingest-corpus`, `INGEST_ENGINE=python\|legacy` flag in Source intake. Seeded profile config fixed to `task_format` in 0011. |

Last full verification run: 644 Python tests (with `DATABASE_URL_TEST`, 0 skipped), 74 TypeScript tests (3 skipped need a DB), lint + typecheck clean; `next build` passes (needs dummy Clerk keys in a bare shell).

**Distractor corpus (done).** 300 hard-negative Northwind documents (60 PDF, 60 DOCX, 45 CSV, 70 MD, 65 TXT; 7 MB; ~1,868 legacy chunks, ~11k structural chunks est.) are generated deterministically by `fixtures/build_distractors.py` (word banks in `fixtures/distractor_src/`). The output, `fixtures/distractors/` and `fixtures/corpus-large/`, is gitignored. `fixtures/distractors.manifest.json` is committed so a fresh clone can verify regenerated bytes (PDF/DOCX hashes depend on reportlab 5.0.1 and python-docx 1.2.0).

Build: `uv run --no-project --with reportlab --with python-docx --with pillow --with pypdfium2 python fixtures/build_distractors.py && python fixtures/assemble_large_corpus.py`.

Verify: `cd intelligence && uv run python -m scribe_eval.distractor_check --distractors ../fixtures/distractors --dataset ../benchmarks/datasets/golden-v1 --manifest ../fixtures/distractors.manifest.json [--no-chunks]`.

Use `--corpus fixtures/corpus-large` for `scripts/eval-baseline.ts` and `scribe-eval ingest-corpus`, with a separate `--out` or profile name. These runs are not comparable to 24-doc runs. Details are in `docs/scribe2/benchmarks/distractors.md`.

Known caveats:

- Bare numbers and a few generic words in `must_include` are exempt from the leak check, since otherwise every doc would be rejected.
- Golden questions eq-03 and eq-05 (liability cap, disputed invoice) have the closest lookalike sentences in distractor contracts, because they name no contract. Human reviewers should confirm they remain unambiguous.
- 5 distractor CSVs fail strict TS `csv-parse` extraction (3 intentionally ragged, 2 with a non-comma delimiter and decimal commas). This is a legitimate Scribe 1.0 weakness for the baseline.

The user's own pre-existing work (not Scribe 2.0) is committed separately: `app/chat/[id]/page.tsx`, `package.json` (jsdom), `package-lock.json`, `__tests__/initial-chat-submit.test.tsx`.

## 3. What must be done by a human (cannot be automated)

1. **Review the golden dataset.** `fixtures/corpus/FACTS.md` and `benchmarks/datasets/golden-v1/README.md` are stamped "NOT YET REVIEWED". Start with `benchmarks/datasets/golden-v1/REVIEW_FIRST.md`. No milestone number should be published until this is done (INV-12).
2. **Vercel preview deploy** following `docs/scribe2/T02-verification.md`: confirms Python co-deploys with Next.js (root `requirements.txt`, rewrite keeps `/api/py` prefix, `functions` config accepted on Hobby, bypass header). If it fails, move Python to a second Vercel project rooted at `intelligence/` and record that in ADR-0002.
3. **Neon branch copy of production**: run migrations `0008` to `0011` and `scripts/backfill-legacy-generations.ts --dry-run` first; check `select extversion from pg_extension where extname='vector'` (iterative scans need ≥ 0.8, else `dense_mode=exact`). The `--driver neon` path of the baseline script and the Neon HTTP executor of the backfill have never run.
4. **Keys** (put them in your shell or `.env.local`; never commit them): `GOOGLE_GENERATIVE_AI_API_KEY` (embeddings, OCR, verifier), `VOYAGE_API_KEY` (rerank; add a payment method to lift the default RPM), `INTERNAL_SERVICE_SECRET`, `BLOB_READ_WRITE_TOKEN` (Python blob download), `VERCEL_AUTOMATION_BYPASS_SECRET` on previews.
5. **DONE 2026-10-12: live T07 baseline** (local Docker DB, real Gemini, Node 18): `docs/scribe2/benchmarks/baseline-1.0.md` (+ `-meta.json`). dev recall@5 0.833, validation 0.903, PRELIMINARY. The `--driver neon` path is still never run. On Node 26, `pdf-parse` intermittently fails valid PDFs, yet vitest needs Node 26.
6. **DONE: embedding-space check** (`scribe_eval/embedding_space_check.py`, results in `T12-notes.md`). Read that file before mixing `gemini-embedding-2` and `-preview` vectors.
7. **Measured so far (PRELIMINARY, `docs/scribe2/benchmarks/m1-prelim.md`)**: profiles A, B, C on dev and validation with real Gemini embeddings on local `eval_live_test`, paired vs 1.0. A and C: dev recall@5 0.993, validation 0.931 / 0.958 (1.0: 0.833 / 0.903). Dev gain over 1.0 is significant, validation mostly not; it confounds chunker and ingestion changes. B (lexical-only) recall@5 0.11: `websearch_to_tsquery` ANDs all terms, so natural questions return nothing, hence hybrid equals dense. Not tuned.
8. **Still needs a human / key**: review the golden set (INV-12); `VOYAGE_API_KEY` for profiles D and E, tau calibration, default-profile choice, flipping `RETRIEVAL_ENGINE`, arming the CI gate (see m1-prelim.md section 9).

## 4. Remaining tasks (T13-T33)

Each task fits one agent run and one PR. The full text is in the plan; the key points follow. Milestones: M1 retrieval (T13-T17), M2 ingestion robustness (T18-T24), M3 evidence/verification/security (T25-T28), M4 lab/perf/release (T29-T33).

**M1: measured hybrid retrieval**
- **T13 (DONE, see table)** Dense (exact/ANN) + lexical (FTS) search in Python, `POST /api/py/retrieve`, one `REPEATABLE READ READ ONLY` transaction with `set_config('app.workspace_id')`, filters inside SQL before LIMIT (INV-2/3), `fts_simple` for identifiers (ERR_AUTH_403, INV-2047) and phrase queries, a `legacy-dense` strategy over `chunk_embeddings_3072` that reproduces the T07 baseline, candidates persisted to `retrieval_candidates`. **Embed queries with `embed_queries`, not the document format.**
- **T14 (DONE except the live Voyage run)** RRF (k=60, ranks only, INV-10) + Voyage rerank adapter (cache, fake for tests, degraded `rerank_unavailable` on failure) + dedup by content_hash/parent + parent expansion + 6,000-token packer; seed retrieval profiles A-E with stored config hashes.
- **T15 (DONE at DB level, see follow-ups below)** RLS: app/Python connect as `scribe_app` (NOBYPASSRLS), policies per INV-1, `scoped(workspaceId)` helper in TS, SECURITY DEFINER queue-claim functions, cross-tenant tests (SQL and every API route). Roll out table by table.
- **T16** `RETRIEVAL_ENGINE=python|legacy` for chat (default legacy), stage-timeline inspector (`rag-inspector.tsx`), "search unavailable" state, Supermemory off in python mode (D16).
- **T17 (PARTIAL, 2026-10-12)** `scribe-eval run-profile` is built and tested (refuses test split, non-`test` DB names and rerank profiles without a Voyage key). Profiles A-C are run and scored on dev/validation (`benchmarks/m1-prelim.md`). D and E, tuning (the lexical AND-query problem), tau, the default choice and the test-split milestone report remain. Original scope: Run profiles A-E on dev/validation, tune, choose default by a written rule, one `--milestone-report` test run → `docs/scribe2/benchmarks/m1.md` with paired CIs, latency, cost, ≥5 worked failures; flip `RETRIEVAL_ENGINE` if the rule is met.

**M2: versioned, recoverable ingestion**
- **T18** Leased resumable steps (claim fn with `lease_token`, fenced writes, `/api/internal/ingest/tick` re-firing itself via `after()`, ≤3 concurrent leases, retry taxonomy, UI stage/attempts/Retry, idempotent embedding usage events). Replace the unique index `ingestion_jobs_file_id_unique` (one job per file) with one per version.
- **T19** Upload new version while old stays searchable; duplicate bytes → `duplicate_of`; newer desired version cancels older build. Note: `source_versions.sha256` is frozen by trigger, so insert the row once bytes are known.
- **T20** Soft delete excludes from retrieval in the same transaction (INV-14); background purge; "Source deleted" citations.
- **T21** OCR for pages <25 chars with images: pypdfium2 150 DPI → Gemini 2.5 Flash, ≤50 pages/source, bounded pages per step, OCR blocks flagged, UI "OCR text: verify against original". Lifts the current `OCR_NOT_AVAILABLE` failure for scanned PDFs.
- **T22 (DONE, nothing run against Neon)** `python -m scribe_ingest.upgrade backfill|progress` queues one structural-v1 job per active Source (T18 machinery drains it); migration `0018` adds `files.reupload_required_at/reason` ("Re-upload to upgrade" note on the Source row); `drizzle/deferred/retire_legacy.sql` (guarded, manual, `-v dry_run=1` supported) deletes legacy 3072 embeddings of upgraded Sources. `scripts/migrate-embeddings.js` removed. `documents` is NOT dropped: the legacy retrieval path, file detail page and source intake still read it (drop is a commented block in the SQL).
- **T23** Open citations at exact page/passage (pdf.js bbox highlight, DOCX/MD/TXT quote highlight, CSV row, "page unavailable").
- **T24** Source detail tabs: Original, Structure, Chunks, Versions, History.

**M3: evidence, verification, security**
- **T25** Evidence bundles with `[E#]` labels unique per turn, answerability gate (reranker top score vs τ calibrated on dev/val), delete the fake "No source links were generated" fallback in `ensureStructuredAnswer` (`app/api/chat/route.ts`), evidence wrapped as data (INV-13).
- **T26** Post-stream claim verification (deterministic label/version/quote-hash checks + one bounded Gemini Flash call, ≤20 claims, 20 s timeout → UNVERIFIED), states via `createUIMessageStream`.
- **T27** Adversarial suite: injection fixtures, 404 on foreign IDs everywhere, token scope/audience tests, log-capture test (no document text in logs), `docs/scribe2/threat-model.md`.
- **T28** Grow golden set to 200, `scribe-eval answer` (correctness, citation P/R, groundedness, abstention matrix), judge agreement κ on 60 human-labelled answers.

**M4: lab, performance, release**
- **T29** Compare 2-5 profiles side by side (`POST /api/py/compare`, UI). **T30** Eval run dashboard with failure categories. **T31 (DONE, NOT ARMED, see section 10)** CI regression gate from cached embeddings/rerank (cache miss fails with refresh message). **T32** Scale/perf report on local Docker pgvector (1k/10k/100k chunks, exact vs HNSW, ef_search, halfvec) and set the `auto` dense-mode threshold. **T33** README/architecture/final benchmark/demo seed script/fresh-clone check; résumé bullets use measured numbers only.

Also owed from earlier tasks: T08 note that `chunk_embeddings_3072` has no ANN index (HNSW max 2000 dims); search it exactly.

## 5. How to run things

```bash
# TypeScript (Node 26 for vitest; npm ci first)
npm run lint && npm run typecheck && npm test
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_Y2xlcmsuZXhhbXBsZS5jb20k CLERK_SECRET_KEY=sk_test_dummy npm run build

# Python (from repo root)
docker compose up -d db            # pgvector/pgvector:pg17 on :54329 (OrbStack: `open -a OrbStack`)
cd intelligence && uv sync
DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/<db> uv run pytest -q
# build a test DB: scripts/apply-migrations.sh (exports schema from lib/db-schema.ts via drizzle-kit),
# then psql -f drizzle/0010_*.sql drizzle/0011_*.sql  (older migrations are NOT replayable from empty)

# Eval
uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1
uv run --project intelligence scribe-eval score --rankings R.jsonl --dataset benchmarks/datasets/golden-v1 --split dev --profile NAME
uv run --project intelligence scribe-eval ingest-corpus --corpus fixtures/corpus --embedder gemini --database-url <EVAL DB> --reset
uv run --no-project --with reportlab --with python-docx --with pillow --with pypdfium2 python fixtures/build_corpus.py
```

Local test databases created in the `scribe-db-1` container: `t08`, `t12_test`, `baseline` (throwaway; contain fake vectors).

## 6. Gotchas learned

- **Disk was nearly full** (the npm cache was cleared once). Check `df -h /System/Volumes/Data` before installs or image pulls.
- **Subagent worktrees branch from upstream (`origin/main`-like), not from this branch.** Work was done in the shared checkout, with disjoint file sets per agent and commits made by the lead. If you use worktrees, merge back and expect `package.json` conflicts.
- **Old migrations (0000-0007) are not replayable** from an empty DB (0004 references columns earlier migrations never create). Test schemas are built from `lib/db-schema.ts` via drizzle-kit export. Squashing the history is an unscheduled cleanup.
- **Fake embeddings** exist for plumbing tests only (`FakeEmbedder`, `BASELINE_FAKE_EMBEDDINGS=1`); they are stamped and refused outside test DBs and the FAKE output dir. `ingest-corpus --embedder fake` can leave fake vectors in an eval DB, so use `--reset` before a real run.
- **`requirements.txt` (repo root) must be kept in sync by hand** with `intelligence/pyproject.toml` runtime deps for the Vercel Python runtime.
- **Metric nuance:** recall@k counts the fraction of a case's evidence entries found, so a perfect single-chunk ranking can score recall@1 = 0.5 on two-evidence cases; hit@k is the any-match version.
- **Gold-corpus defects fixed so far:** Warsaw on-time-delivery prose contradicted its table (fixed in `fixtures/corpus_src/q3-operations-report.md`).
- Corpus quirk found by the baseline harness: the old pipeline fails to extract the scanned PDF, `office-locations.csv` (ragged) and `price-list-2026.csv` (`;` delimiter plus BOM). These are real Scribe 1.0 weaknesses, not harness bugs.
- `chunking.py` imports `scribe_eval.normalize`; ideally move `normalize` to a shared module.
- Seeded embedding profiles are immutable (trigger). If a profile is wrong after it has been applied anywhere real, add a new profile row instead of editing.

## 7. Working agreements

- Use Sonnet-class models for everything.
- Commit WITHOUT any Co-Authored-By or AI attribution line. This was a later user decision; earlier commits have one.
- Roles: one Opus head only oversees and writes no code, and reviews once after ALL tasks are done (not per task). One main Sonnet builder does the work and Haiku subagents do easy tasks. The orchestrator re-runs tests itself before every commit.
- Do not commit secrets, and do not commit fake-embedding results as baselines.
- Do not tune on the test split.
- Report outcomes as measured, including regressions.

## 8. T15 follow-ups (RLS, migration 0013)

0013 enables RLS (no FORCE) and adds `scribe_app`, `scribe_definer` and the `app_*` SECURITY DEFINER functions. Rollout order: apply 0013, move callers to `scoped()` or a `scribe_app` DSN (enable LOGIN out of band), and only then run `drizzle/deferred/force_rls.sql` (it has a guard and is not journaled). Open items:

- **Never run `drizzle-kit push`** against a DB with these policies/roles/functions: Drizzle does not model them and push may drop or reset them. Use `generate` + hand-written SQL only.
- DONE: cross-workspace integrity. `drizzle/0019_workspace_composite_fks.sql` adds `UNIQUE (id, workspace_id)` on the eight parents and 23 composite FKs `(col, workspace_id) -> parent(id, workspace_id)` (rollback in `drizzle/rollback/`). It pre-checks existing data and aborts with per-relationship counts and a finder query, so run it on a Neon branch copy first. Nullable SET NULL columns use `ON DELETE SET NULL (col)` (PG15+; skipped with a NOTICE on older servers). Tests: `intelligence/tests/schema/test_t19_composite_fks.py`; the nosuperuser script covers it.
- DONE: DB integration test for `lib/scoped.ts`: `__tests__/scoped.db.test.ts` (skips without `DATABASE_URL_TEST` + psql; runs the batch as `scribe_app` via a psql shim because the app's `db` is neon-http).
- DONE: guard `intelligence/tests/schema/test_workspace_scope_guard.py` fails when a table has an FK into a workspace-scoped parent but neither `workspace_id` nor an RLS policy.
- No TS callers use `scoped()` yet; `ensurePersonalWorkspace` should call `app_ensure_personal_workspace` once on `scribe_app`. `scribe_eval.ingest_corpus.ensure_eval_workspace` needs a privileged connection.

## 9. T26 claim verification (notes)

Flow: Python-engine chat streams the model, then a `data-verification` part (`verifying` -> `done|unavailable`, same part id) via `createUIMessageStream`, then `finish`. Claims are split in TS (`splitClaims`), the service re-extracts `[E#]` labels from claim text. Statuses: VERIFIED, PARTIAL, UNSUPPORTED, UNCITED, INVALID_CITATION, UNVERIFIED. VERIFIED is produced only in `_final_status` (deterministic ok AND verdict SUPPORTED). More than 20 eligible claims: the first 20 go in one call, the rest are UNVERIFIED. Timeout 20 s, provider error, malformed verdict, or no key: UNVERIFIED. If /verify itself is unreachable the UI shows "Verification unavailable". Stored messages are read back through `/verify/read` (matched to re-split claims by text).

**Decision (T25 follow-up): the verifier receives only the STORED QUOTE (the cited child span), not the expanded parent text the answering model saw.** The quote is what the user sees on click and what the hash and span checks cover. A claim supported only by sibling text of the parent will come out PARTIAL or UNSUPPORTED. That is conservative and never inflates a verdict. Giving the verifier the parent (one extra join from `chunks.parent_id`) is possible, but a claim could then count as supported by text hidden behind the citation. Revisit this with a measured false-UNSUPPORTED rate in T28.

Known limits:

- Not run against the live Gemini API. The real client, `GeminiVerifier`, is tested with a mock transport only.
- "Wrong version" compares the bundle's version with the stored one, and the item with its chunk. It does not require the version to be the currently active one.
- Ingestion-heavy Python tests make `test_verify.py` take about 1 minute.

## 10. T31 retrieval regression gate (built, deliberately NOT ARMED)

`scribe-eval gate` + CI job `retrieval-gate` (`.github/workflows/ci.yml`). Rules, power limits and arming steps are in `docs/scribe2/release-rules.md`, file procedures in `benchmarks/gate/README.md`, and code in `intelligence/src/scribe_eval/gate.py`. The gate replays cached Gemini embedding and Voyage rerank responses (no provider keys, no network; a cache miss FAILS with a refresh message). It indexes the corpus into a throwaway eval workspace plus an identical-content decoy workspace, runs the validation split through the real `hybrid-rrf-rerank` strategy, and compares the result with `benchmarks/gate/baseline.json`. It fails if the 95% paired-bootstrap CI upper bound of (current - baseline) is below 0 for Recall@5 or for MRR, if there is any cross-workspace hit, if missed abstentions increase, or if the baseline is not comparable (dataset, profile or case ids differ, or a previously indexed document now fails).

**Today the job passes with a `GATE NOT ARMED` warning annotation and protects nothing**, because no real cache or baseline exists (they need your keys and, ideally, the golden-set review). A seeded or fake baseline never arms it. Until T17, the weakest part is `tau`: it is the uncalibrated 0.0, so the abstention rule (R3) cannot trip.

**To arm it (you, locally; about one corpus embedding pass plus 40 queries and rerank calls):** after reviewing the golden set (section 3, item 1), `docker compose up -d db`, create a throwaway DB whose name contains `test`, `scripts/apply-migrations.sh <url>`, then with `GOOGLE_GENERATIVE_AI_API_KEY` and `VOYAGE_API_KEY` exported run `uv run --project intelligence scribe-eval gate --refresh-cache --database-url <url>`; unset the keys; run `... gate --write-baseline --database-url <url>` and `... gate --database-url <url>` (must pass); commit `benchmarks/gate/cache.jsonl.gz` and `benchmarks/gate/baseline.json`. Mark `retrieval-gate` a required status check on `main` in GitHub settings.

Gotchas:

- Any PR that changes chunking, document text, case questions, or the rerank input (fusion order, candidate pool) needs a refreshed cache, because a miss fails.
- The scanned PDF cannot be indexed in replay (OCR is not cached), so the baseline records it as a known failure.
- The gate never scores the test split.
- Tests use a seeded synthetic fixture built at test time (`intelligence/tests/evaluation/_gate_seed.py`, committed seeded baseline `tests/evaluation/gate_seeded/baseline.seeded.json`, 3.4 KB). The seeded cache (~270 KB) is rebuilt in a temp dir and not committed.
- The CI job itself has not yet run on GitHub.

## 11. Opus end-of-project review fixes

This section records the status of that review.

**Owner steps before merge or deploy (Opus P0-2).** Do these first; nothing in Scribe 2.0 has run against Neon.

1. Run `drizzle-kit migrate` (or the hand-applied SQL for 0008-0018 in order) on a **Neon branch copy of production**, never on production first. Merging this branch to `main` may auto-deploy through Vercel and the new code expects the new schema, so either do the Neon-branch rehearsal below BEFORE merging, or retarget the PR at a non-deploying branch so a merge does not deploy.
2. On that branch copy confirm: (a) the contents of `drizzle.__drizzle_migrations` (which migrations production believes it has run; the old history 0000-0007 is not replayable and the `created_at` values drizzle compares must line up with `drizzle/meta/_journal.json`, otherwise drizzle will try to re-run old migrations); (b) `show server_version` (the Postgres major version: the migrations were only verified on pg17 in Docker; the role-membership syntax branches on PG16); (c) `select extversion from pg_extension where extname = 'vector'` (iterative scans need >= 0.8).
3. **Neon owner BYPASSRLS check.** Run `select rolname, rolsuper, rolbypassrls, rolcreaterole from pg_roles where rolname = current_user;` as the connection role the app and migrations use. Neon's default owner role typically has BYPASSRLS, which means the RLS policies from 0013 do not apply to it even after `drizzle/deferred/force_rls.sql` (FORCE does not override BYPASSRLS). Real database-enforced isolation needs the app and the Python service to connect as `scribe_app` (enable LOGIN out of band). Until then the README/threat-model wording stays: policies enabled, not enforced for the owner connection used today (R1).
4. Only then merge/deploy, with `INGEST_ENGINE=legacy` (the default) first, then flip to `python` on a preview.

**What the review fixes changed (all uncommitted at the time of writing).**

- **P0-3 delete is a real delete again unless the Python engine is configured.** `lib/ingestion/source-delete.ts`: `pythonEngineConfigured()` is `INGEST_ENGINE=python` AND `INTERNAL_SERVICE_SECRET`. `deleteSource` hard-deletes (blobs of the original and every version, then ONE statement that scrubs evidence quotes and trace previews and deletes the `files` row, which cascades) otherwise, and soft-deletes (INV-14, Python purge after the grace period) only when the Python engine is configured. A blob failure falls back to a soft delete (hidden at once; purge backoff columns record it). The daily sweep route answers 200 with `note: "python engine not configured"` in legacy mode instead of calling Python, and runs `purgeSoftDeletedSources()` (idempotent, 20 rows per sweep, honours the 0016 backoff) to hard-purge rows left soft-deleted by the interim behaviour. The SQL is also executed against Postgres by `intelligence/tests/integration/test_legacy_hard_delete.py`.
- **P0-1 migrations 0013, 0014, 0016 apply and re-apply as a NOSUPERUSER CREATEROLE role.** They were edited in place (they had never been applied to any real database, so no journal change or new migration was needed): a preamble makes the migrating role a member of `scribe_definer` with INHERIT and SET (PG16+; plain GRANT before) and lends CREATE on schema public, removed again at the end. `scripts/test-migrations-nosuperuser.sh` proves it in a throwaway database `scribe_nosuper_test` with a throwaway role `scribe_migr_test` (both dropped afterwards); CI runs it first in the python job. Verified on pg17 in Docker only; Neon's Postgres version and its owner-role privileges are unknown.
- **P1-1** the "foreign claimable jobs" guard is one helper (`tests/integration/_db.py`): FAILS when the `CI` env var is set, skips locally with a message naming `purge_workspaces`.
- **P1-3** docs say the shipped ANN query path was measured not to use HNSW; README says RLS is not enforced for the owner connection used today.
- **P2** activation locks the files row before the job row (matches `scribe_soft_delete_source`; test holds the files lock and checks the job row is still free); blob downloads are allowlisted and do not follow redirects; the FastAPI schema/docs routes are off; `Content-Disposition` is encoded in all three branches of `/api/files/[id]/view`; `fail_generation` deliberately has no lease predicate (documented in the code: it only runs after the fenced terminal write).

**Production incident.** 2026-10-12: PR #10 was merged to main and auto-deployed to production; prod broke (relation workspaces does not exist) because migrations 0008+ never ran on Neon. Production was rolled back to the pre-merge deployment scribe-9wz4vw60w; Vercel production auto-assign is paused. Do not promote a main deploy until the Neon-branch migration rehearsal in this section is done.

# Scribe 2.0 handover

Everything a new engineer (or a new Claude session) needs to continue. Read this first, then `docs/scribe2/DESIGN.md` (source of truth for decisions, data model, invariants) and the ADRs in `docs/adr/0002–0012`.

Repo: `github.com/marsh15/SCRIBE` · live app: https://scribe-marsh.vercel.app/ · working branch: `scribe2/m0-foundation` (branched from `codex/production-readiness` @ `62ccb1a`; upstream `main` = `0edddb1`). Nothing in Scribe 2.0 has been deployed or run against Neon or the real Gemini/Voyage APIs yet.

## 1. What Scribe 2.0 is

An upgrade of the existing Scribe RAG app (Next.js 16, Clerk, Drizzle, Neon Postgres + pgvector, Vercel Blob, AI SDK, Gemini) into a measured, citation-verifiable retrieval system:

- Hybrid retrieval (dense + Postgres FTS, fused by RRF, reranked by Voyage rerank-2.5) owned by a Python service.
- Versioned sources and index generations with atomic activation (no more delete-then-reindex).
- Exact citations (page, bbox, row) from source spans; evidence labels `[E1]`; post-stream claim verification.
- Personal workspaces enforced by Postgres RLS.
- A labeled evaluation set, metrics CLI, and CI regression gate so every "better" claim is measured.

Hard constraints (user decisions): stays on **Vercel Hobby** (300 s functions, 500 MB Python bundle, once-daily cron, non-commercial so `FEATURE_BILLING_ENABLED` stays off), one deployment, light parsers + Gemini OCR (no Docling), Voyage reranker behind an adapter, existing production data preserved and backfilled as version 1, 14-week milestone plan.

## 2. What has been done (15 commits on `scribe2/m0-foundation`)

| Task | Commit | Result |
|---|---|---|
| T01 | `e137ba6` | `docs/scribe2/DESIGN.md` (decisions D1–D22, data model, ingestion step machine, query path, invariants INV-1…16, edge-case table), ADRs 0002–0012, `BASELINE_1.0.md` (current behaviour with file:line refs), glossary additions in `CONTEXT.md`. |
| T02 | `a1060f6` | FastAPI service in `intelligence/` (uv, Python 3.12), thin `api/py/index.py` entry, `/api/py/*` rewrite in `vercel.json`, 60 s HS256 internal tokens (`lib/internal-token.ts`, `lib/intelligence-client.ts`, secret `INTERNAL_SERVICE_SECRET`), `docker-compose.yml` (pgvector pg17, port 54329), `.github/workflows/ci.yml`, `scripts/apply-migrations.sh`, `docs/scribe2/T02-verification.md` (**preview-deploy checklist — not yet run**). |
| T03 | `935b84f` | `workspaces` / `workspace_members`, `workspace_id` on files/chats/rag_traces, migration `0008` with backfill, `lib/workspace.ts` (`ensurePersonalWorkspace`, `resolveWorkspace`), all TS queries scoped. `/api/chat` now 404s on a chat id the caller does not own. |
| T04 | `1c20438` | Legacy ingestion removed: `lib/ingestion/worker.ts`, `batch.ts`, old scripts deleted; `/api/uploads/{sign,complete}` and `/api/ingest/batch*` return 410; migration `0009` marks stranded legacy sources failed. lint/vitest now ignore `.claude/`. |
| T05 | `9b82680` | Eval corpus `fixtures/corpus/` (24 Northwind Logistics docs, 5 formats + scanned PDF with `.truth.txt`, `FACTS.md`, built by `fixtures/build_corpus.py`), `benchmarks/datasets/golden-v1/` (120 cases, 10 categories × 12, 40/20/40 dev/validation/test split, seed 20260101), `scribe-eval validate` and `assign-splits`. |
| T06 | `35422f0` | `scribe_eval`: metrics (recall/hit@k, MRR, nDCG@10, P@5, CIs), paired bootstrap, run records (INV-11), response cache with replay mode (D18), `scribe-eval score` / `compare`; `--split test` refused without `--milestone-report`. |
| T07 (harness only) | `2ee1051` | `scripts/eval-baseline.ts`: ingests the corpus with the *current* TS pipeline into an eval workspace and writes rankings for threshold 0.3 and no-threshold. **Live run not done** (needs key + DB). |
| T08 | `a5f4183` | Migrations `0010` (all B2 tables, FTS generated columns, HNSW on 1536, immutability triggers) and `0011` (seed profiles), `scripts/backfill-legacy-generations.ts`, Python schema tests, `T08-notes.md`. |
| T09 | `d1ad4c2` | `scribe_ingest.parse(bytes, mime)` for PDF/Markdown/TXT: typed blocks, heading paths, exact pages, bbox, furniture detection, canonical-text spans, OCR-candidate pages, `download_private_blob`. |
| T10 | `2ff0383` | DOCX (document order, headings, header-first tables) and CSV (BOM, delimiter sniffing, ragged rows, 5,000-row limit, raw record kept in `meta["raw"]`). |
| — | `6bce48e` | D17 refinement: CSV evidence quotes (raw lines) match a chunk when all non-empty cells appear in order. |
| T11 | `312293c` | `chunk_document`: ≤384-token children inside section parents, table headers repeated, block-relative spans, vendored `o200k_base` vocabulary (no network), Python port of the legacy 4000/400 chunker verified against TS on 27 fixtures. |
| T13 | see git log | `retrieval/{dense,lexical,search,queries,registry}.py`, `POST /retrieve` (strategies `dense-exact`, `dense-ann`, `lexical-v1`, `legacy-dense`), snapshot isolation, scoping tests; see `T13-notes.md`. |
| T14 (pure half) | see git log | RRF, Voyage rerank adapter, dedup, packing, profiles A–E definitions, `pipeline.py`; see `T14-notes.md`. **Still to do for T14:** register hybrid handlers under `retrieval/strategies/` (`@register("hybrid-rrf-rerank")` etc.), DB-backed `upsert_profiles` test (config_hash differs from the SQL `sha256(jsonb::text)` style), parent loader SQL, live Voyage run. |
| T12 | `1a39fb6` | `GeminiEmbedder` (gemini-embedding-2, 1536 dims, text-prefix task format), staged idempotent indexer with the B3 activation transaction, `POST /ingest/source`, `scribe-eval ingest-corpus`, `INGEST_ENGINE=python\|legacy` flag in Source intake. Seeded profile config fixed to `task_format` in 0011. |

Verification at last full run: 370 Python tests (with `DATABASE_URL_TEST`), 74 TypeScript tests (3 skipped need a DB), lint + typecheck clean; `next build` passes (needs dummy Clerk keys in a bare shell).

**Distractor corpus (done):** 300 hard-negative Northwind documents (60 PDF, 60 DOCX, 45 CSV, 70 MD, 65 TXT; 7 MB; ~1,868 legacy chunks, ~11k structural chunks est.) generated deterministically by `fixtures/build_distractors.py` (word banks in `fixtures/distractor_src/`). Output `fixtures/distractors/` and `fixtures/corpus-large/` are gitignored; `fixtures/distractors.manifest.json` is committed so a fresh clone can verify regenerated bytes (PDF/DOCX hashes depend on reportlab 5.0.1 / python-docx 1.2.0). Build: `uv run --no-project --with reportlab --with python-docx --with pillow --with pypdfium2 python fixtures/build_distractors.py && python fixtures/assemble_large_corpus.py`. Verify: `cd intelligence && uv run python -m scribe_eval.distractor_check --distractors ../fixtures/distractors --dataset ../benchmarks/datasets/golden-v1 --manifest ../fixtures/distractors.manifest.json [--no-chunks]`. Use `--corpus fixtures/corpus-large` for `scripts/eval-baseline.ts` and `scribe-eval ingest-corpus` (separate `--out`/profile name; not comparable to 24-doc runs). Details in `docs/scribe2/benchmarks/distractors.md`. Known caveats: bare numbers and a few generic words in `must_include` are exempt from the leak check (otherwise every doc would be rejected); golden questions eq-03 and eq-05 (liability cap / disputed invoice) have the closest lookalike sentences in distractor contracts since they name no contract, so human reviewers should confirm they remain unambiguous; 5 distractor CSVs fail strict TS `csv-parse` extraction (3 intentionally ragged, 2 with non-comma delimiter + decimal commas), which is a legitimate Scribe 1.0 weakness for the baseline.

Also committed as a separate commit, the user's own pre-existing work (not Scribe 2.0): `app/chat/[id]/page.tsx`, `package.json` (jsdom), `package-lock.json`, `__tests__/initial-chat-submit.test.tsx`.

## 3. What must be done by a human (cannot be automated)

1. **Review the golden dataset.** `fixtures/corpus/FACTS.md` and `benchmarks/datasets/golden-v1/README.md` are stamped "NOT YET REVIEWED". Start with `benchmarks/datasets/golden-v1/REVIEW_FIRST.md`. No milestone number should be published until this is done (INV-12).
2. **Vercel preview deploy** following `docs/scribe2/T02-verification.md`: confirms Python co-deploys with Next.js (root `requirements.txt`, rewrite keeps `/api/py` prefix, `functions` config accepted on Hobby, bypass header). If it fails, move Python to a second Vercel project rooted at `intelligence/` and record that in ADR-0002.
3. **Neon branch copy of production**: run migrations `0008`–`0011` and `scripts/backfill-legacy-generations.ts --dry-run` first; check `select extversion from pg_extension where extname='vector'` (iterative scans need ≥ 0.8, else `dense_mode=exact`). The `--driver neon` path of the baseline script and the Neon HTTP executor of the backfill have never run.
4. **Keys** (put in your shell or `.env.local`, never commit): `GOOGLE_GENERATIVE_AI_API_KEY` (embeddings, OCR, verifier), `VOYAGE_API_KEY` (rerank; add a payment method to lift the default RPM), `INTERNAL_SERVICE_SECRET`, `BLOB_READ_WRITE_TOKEN` (Python blob download), `VERCEL_AUTOMATION_BYPASS_SECRET` on previews.
5. **Run the live T07 baseline** (`docs/scribe2/benchmarks/baseline-1.0-howto.md`), then write `docs/scribe2/benchmarks/baseline-1.0.md`. Needs a throwaway DB (never production) and **Node 18** (`PATH=/usr/local/bin:$PATH`): on Node 26 `pdf-parse` fails valid PDFs intermittently, while vitest needs Node 26.
6. Measure whether `gemini-embedding-2` (1536) and `gemini-embedding-2-preview` vectors are compatible (a ~20-line script is described in `T12-notes.md`; not written). Until measured they are separate spaces.

## 4. Remaining tasks (T13–T33)

Each fits one agent run / one PR. Full text is in the plan; key points below. Milestones: M1 retrieval (T13–T17), M2 ingestion robustness (T18–T24), M3 evidence/verification/security (T25–T28), M4 lab/perf/release (T29–T33).

**M1 — measured hybrid retrieval**
- **T13 (DONE — see table)** Dense (exact/ANN) + lexical (FTS) search in Python, `POST /api/py/retrieve`, one `REPEATABLE READ READ ONLY` transaction with `set_config('app.workspace_id')`, filters inside SQL before LIMIT (INV-2/3), `fts_simple` for identifiers (ERR_AUTH_403, INV-2047) and phrase queries, a `legacy-dense` strategy over `chunk_embeddings_3072` that reproduces the T07 baseline, candidates persisted to `retrieval_candidates`. **Embed queries with `embed_queries`, not the document format.**
- **T14 (pure half DONE; wiring/route/live run remain)** RRF (k=60, ranks only, INV-10) + Voyage rerank adapter (cache, fake for tests, degraded `rerank_unavailable` on failure) + dedup by content_hash/parent + parent expansion + 6,000-token packer; seed retrieval profiles A–E with stored config hashes.
- **T15** RLS: app/Python connect as `scribe_app` (NOBYPASSRLS), policies per INV-1, `scoped(workspaceId)` helper in TS, SECURITY DEFINER queue-claim functions, cross-tenant tests (SQL and every API route). Roll out table by table.
- **T16** `RETRIEVAL_ENGINE=python|legacy` for chat (default legacy), stage-timeline inspector (`rag-inspector.tsx`), "search unavailable" state, Supermemory off in python mode (D16).
- **T17** Run profiles A–E on dev/validation, tune, choose default by a written rule, one `--milestone-report` test run → `docs/scribe2/benchmarks/m1.md` with paired CIs, latency, cost, ≥5 worked failures; flip `RETRIEVAL_ENGINE` if the rule is met.

**M2 — versioned, recoverable ingestion**
- **T18** Leased resumable steps (claim fn with `lease_token`, fenced writes, `/api/internal/ingest/tick` re-firing itself via `after()`, ≤3 concurrent leases, retry taxonomy, UI stage/attempts/Retry, idempotent embedding usage events). Replace the unique index `ingestion_jobs_file_id_unique` (one job per file) with one per version.
- **T19** Upload new version while old stays searchable; duplicate bytes → `duplicate_of`; newer desired version cancels older build. Note: `source_versions.sha256` is frozen by trigger, so insert the row once bytes are known.
- **T20** Soft delete excludes from retrieval in the same transaction (INV-14); background purge; "Source deleted" citations.
- **T21** OCR for pages <25 chars with images: pypdfium2 150 DPI → Gemini 2.5 Flash, ≤50 pages/source, bounded pages per step, OCR blocks flagged, UI "OCR text — verify against original". Lifts the current `OCR_NOT_AVAILABLE` failure for scanned PDFs.
- **T22** Backfill legacy sources onto structural-v1, then drop `chunk_embeddings_3072`, `documents`, and `scripts/migrate-embeddings.js` (back up the Neon branch first).
- **T23** Open citations at exact page/passage (pdf.js bbox highlight, DOCX/MD/TXT quote highlight, CSV row, "page unavailable").
- **T24** Source detail tabs: Original, Structure, Chunks, Versions, History.

**M3 — evidence, verification, security**
- **T25** Evidence bundles with `[E#]` labels unique per turn, answerability gate (reranker top score vs τ calibrated on dev/val), delete the fake "No source links were generated" fallback in `ensureStructuredAnswer` (`app/api/chat/route.ts`), evidence wrapped as data (INV-13).
- **T26** Post-stream claim verification (deterministic label/version/quote-hash checks + one bounded Gemini Flash call, ≤20 claims, 20 s timeout → UNVERIFIED), states via `createUIMessageStream`.
- **T27** Adversarial suite: injection fixtures, 404 on foreign IDs everywhere, token scope/audience tests, log-capture test (no document text in logs), `docs/scribe2/threat-model.md`.
- **T28** Grow golden set to 200, `scribe-eval answer` (correctness, citation P/R, groundedness, abstention matrix), judge agreement κ on 60 human-labelled answers.

**M4 — lab, performance, release**
- **T29** Compare 2–5 profiles side by side (`POST /api/py/compare`, UI). **T30** Eval run dashboard with failure categories. **T31** CI regression gate from cached embeddings/rerank (cache miss fails with refresh message). **T32** Scale/perf report on local Docker pgvector (1k/10k/100k chunks, exact vs HNSW, ef_search, halfvec) and set the `auto` dense-mode threshold. **T33** README/architecture/final benchmark/demo seed script/fresh-clone check; résumé bullets use measured numbers only.

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

- **Disk was nearly full** (cleared the npm cache once). Check `df -h /System/Volumes/Data` before installs or image pulls.
- **Subagent worktrees branch from upstream (`origin/main`-like), not from this branch.** Work was done in the shared checkout with disjoint file sets per agent and commits made by the lead; if you use worktrees, merge back and expect `package.json` conflicts.
- **Old migrations (0000–0007) are not replayable** from an empty DB (0004 references columns earlier migrations never create). Test schemas are built from `lib/db-schema.ts` via drizzle-kit export. Squashing the history is an unscheduled cleanup.
- **Fake embeddings** exist for plumbing tests only (`FakeEmbedder`, `BASELINE_FAKE_EMBEDDINGS=1`); they are stamped and refused outside test DBs / the FAKE output dir. `ingest-corpus --embedder fake` can leave fake vectors in an eval DB: use `--reset` before a real run.
- **`requirements.txt` (repo root) must be kept in sync by hand** with `intelligence/pyproject.toml` runtime deps for the Vercel Python runtime.
- **Metric nuance:** recall@k counts the fraction of a case's evidence entries found, so a perfect single-chunk ranking can score recall@1 = 0.5 on two-evidence cases; hit@k is the any-match version.
- **Gold-corpus defects fixed so far:** Warsaw on-time-delivery prose contradicted its table (fixed in `fixtures/corpus_src/q3-operations-report.md`).
- Corpus quirk found by the baseline harness: the old pipeline fails to extract the scanned PDF, `office-locations.csv` (ragged) and `price-list-2026.csv` (`;` + BOM); these are real Scribe 1.0 weaknesses, not harness bugs.
- `chunking.py` imports `scribe_eval.normalize`; ideally move `normalize` to a shared module.
- Seeded embedding profiles are immutable (trigger). If a profile is wrong after it has been applied anywhere real, add a new profile row instead of editing.

## 7. Working agreements

Use Sonnet-class models for everything. Commit with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Do not commit secrets or fake-embedding results as baselines. Do not tune on the test split. Report outcomes as measured, including regressions.

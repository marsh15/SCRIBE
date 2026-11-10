# Resume and summary bullets: measured or literal facts only

Every bullet below is one of three things: (a) a count taken from a command run on 2026-11-10 on the combined working tree described in `FINISH_PLAN.md`, (b) a literal fact visible in the repository, or (c) a figure copied from a committed result file, with its limits. Anything about quality, accuracy, latency against real providers, cost, or "improvement over Scribe 1.0" is **pending measurement** and has no number here. Do not add one until the matching command in `docs/scribe2/benchmarks/final.md` has been run on a reviewed golden set.

## Safe to use now

Counts (re-run to confirm before quoting; the commands are in the README):
- 2,282 Python tests pass, 1 skipped, 1 expected failure, twice in a row, against a local pgvector Postgres (`uv run pytest -q`, 2026-11-10; 88 test files).
- 508 TypeScript tests pass (vitest, 60 files run; 17 tests skip in a plain `npm test` because they need a database; the database-backed suites pass when run: `scripts/test-chat-rls.py` (14 tests) and `__tests__/scoped.db.test.ts` (4 tests, with `DATABASE_URL_TEST` set)); `tsc --noEmit` and `eslint` report no errors.
- 98 commits between the pre-2.0 build `0edddb1` and the `main` commit this work started from (`git rev-list --count 0edddb1..655e157`; includes merge commits).

Built (literal repo facts):
- Python FastAPI retrieval and ingestion service with 10 endpoints, authenticated by audience-bound HS256 tokens that Next.js mints with a 60-second lifetime.
- Hybrid retrieval: dense search (an exact scan; an HNSW index exists, but the shipped ANN query path was measured NOT to use it, see the performance bullet) plus Postgres full-text search, reciprocal rank fusion (k=60), a Voyage rerank adapter with a fake for tests, dedup, 6,000-token context packing, and five seeded retrieval profiles.
- Versioned sources with index generations and a single-transaction activation; leased, resumable ingestion steps with a retry taxonomy; soft delete that removes a source from search in the same transaction.
- Parsers for PDF, Markdown, TXT, DOCX and CSV with exact page, bounding box and row provenance; OCR path for scanned pages; parent/child chunker with source spans.
- Evidence labels `[E#]`, an answerability gate (threshold uncalibrated), and claim verification after the stream, with six statuses. "Verified" requires both deterministic checks and a verifier verdict.
- Postgres row-level-security policies, a NOBYPASSRLS application role and SECURITY DEFINER queue functions. The policies are not forced and the deployed runtime still connects as the table owner. The TypeScript chat, file, ingestion and version code and the Python endpoints pass cross-workspace tests as a real non-owner login locally (`scripts/test-chat-rls.py`, `scripts/test-python-rls.py`); not run on Neon.
- Evaluation toolkit: 24-document synthetic corpus, 200-case golden set across 10 categories (80 cases machine-drafted and unreviewed), metrics with paired bootstrap confidence intervals, answer-level scoring, run dashboard, 2-5 profile compare lab, CI regression gate (not armed), scaling harness.
- Security work: an adversarial-document suite, an IDOR sweep over every route and server action, a token-rejection matrix of about 550 parametrised cases, log-canary tests, and a written threat model with 15 documented residual risks. This was an engineering review with tests, not a penetration test.
- 12 architecture decision records, 21 SQL migrations (`0000`-`0020`), a documented 14-week milestone plan.

Measured, with limits (from `docs/scribe2/benchmarks/performance.md`, synthetic data, one Mac laptop, OrbStack Postgres 17 with pgvector 0.8.7, scales 1k/10k/30k chunks, 100k not measured, not Neon, not Vercel, not real embeddings):
- Exact dense search about 14 microseconds per eligible chunk, linear: p50 9.9 ms at 700 chunks, 79 ms at 7,000, 300 ms at 21,000.
- The shipped ANN query did not use the HNSW index in any of 15 measured cases. Forced HNSW was 3x to 3.4x faster than exact for a large workspace and slower for a small workspace in a large table.
- halfvec(1536) halves per-embedding storage (3,076 vs 6,148 bytes) with exact-halfvec vs exact-vector recall@10 of 0.999 to 1.0 on this synthetic data.

## Pending measurement (no numbers; do not invent)

| Claim one might want to make | What is needed | Status |
|---|---|---|
| Retrieval Recall@k / MRR / nDCG of Scribe 2.0 | Reviewed golden set, real keys, T17, `final.md` commands | not yet measured |
| Improvement over Scribe 1.0 (any percentage) | Live 1.0 baseline (`baseline-1.0-howto.md`), then paired bootstrap | not yet measured |
| Citation precision, groundedness, abstention quality | `scribe-eval answer` on a reviewed set with real providers | not yet measured |
| Claim-verification accuracy (false-VERIFIED rate) | `scribe-eval judge-sheet` and 60 human labels | not yet measured |
| End-to-end latency, cost per query | Run against Gemini, Voyage and Neon | not yet measured |
| Behaviour at 100,000 chunks | Re-run `scribe-perf` with at least 8 GB free disk | not yet measured |
| Production readiness on Vercel Hobby | Preview deploy per `T02-verification.md` | not yet verified |
| Regression-gate protection | Arm the gate (`release-rules.md`) | not armed |

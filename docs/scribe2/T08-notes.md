# T08 notes: source versions, profiles, index generations, legacy backfill

Files: `lib/db-schema.ts`, `drizzle/0010_source_versions_generations.sql`, `drizzle/0011_seed_profiles.sql` (+ `_journal.json`), `scripts/backfill-legacy-generations.ts`, `__tests__/backfill-legacy-generations.test.ts`, `intelligence/tests/schema/test_t08_schema.py`.

## pgvector

- Local (`pgvector/pgvector:pg17`, Postgres 17.11): **pgvector 0.8.7** (the version reported by `pg_extension.extversion`).
- **The Neon version has not been checked.** Before T13 relies on `hnsw.iterative_scan` (needs pgvector >= 0.8.0) run on the Neon branch: `SELECT extversion FROM pg_extension WHERE extname='vector';`. If it is below 0.8, set `dense_mode=exact` (Design D11) until Neon upgrades (`ALTER EXTENSION vector UPDATE`).
- HNSW max is 2000 dims, so `chunk_embeddings_3072` has no ANN index (exact scan only). `chunk_embeddings_1536` has `hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)`.

## Migrations

- `0010` is hand-written and idempotent (`IF NOT EXISTS`, `DO ... EXCEPTION WHEN duplicate_object`, `DROP TRIGGER IF EXISTS` + create). It starts with a pgvector check that raises `T08 migration requires the pgvector extension ...` if the extension is absent and cannot be created.
- `0011` seeds profiles with `ON CONFLICT DO NOTHING` and fixed UUIDs (`e0000000-...-01/02` embedding, `c0000000-...-01/02` chunk, `10000000-...-01/02` index). `retrieval_profiles` is created empty (T14 seeds A-E).
- Seeded: embedding `legacy-gemini-embedding-2-preview-3072`, `gemini-embedding-2-1536`; chunk `legacy-char-4000-400`, `structural-v1` (child <= 384 tokens, overlap 48 only when a block is split, parent ~1536, `o200k_base` recorded as a proxy); index `legacy-v1` (legacy chunk + legacy embedding, parser `legacy-ts-v1`), `structural-v1`.
- Verified: (1) a DB built from the PREVIOUS `lib/db-schema.ts` plus legacy fixture rows, then 0010 and 0011 applied twice, is identical (columns with types/defaults/generation expressions, indexes, constraints) to a DB built from `scripts/apply-migrations.sh` (drizzle-kit export of the NEW schema). (2) 0010/0011 also run twice on top of the export-built DB. Triggers and seed rows exist only via the SQL files, so the Python schema tests need `apply-migrations.sh` followed by `psql -f` of 0010 and 0011 (command in the test docstring).
- No `drizzle/meta` snapshot is added (0003-0009 have none either; journal entries only).
- Composite FK targets are `UNIQUE` constraints, not `CREATE UNIQUE INDEX`: drizzle-kit export emits FK `ALTER`s before standalone indexes, so a unique index would have broken `apply-migrations.sh` for everyone.

## Design choices

- **One ACTIVE generation per (source, index profile) (INV-4).** B2 gives `index_generations` only `source_version_id`, so a partial unique index cannot see the source. I added a denormalised `source_id` column, a composite FK `(source_version_id, source_id) -> source_versions(id, source_id)` so it cannot disagree with the version, and `UNIQUE (source_id, index_profile_id) WHERE status = 'ACTIVE'`. `active_indexes` PK `(source_id, index_profile_id)` gives the pointer uniqueness, and its composite FK `(generation_id, source_id, index_profile_id) -> index_generations` forces the pointer to a generation of the same source and profile. The ACTIVATE transaction must mark the old generation `SUPERSEDED` before setting the new one `ACTIVE` (the unique index is immediate). Nothing forces `active_indexes` to point at a generation whose status is ACTIVE; that remains the ACTIVATE transaction's job (T18).
- `UNIQUE (source_version_id, index_profile_id)` on `index_generations` (ADR-0011: one generation per version and profile). A failed build is retried by resetting that row, not by inserting a second one. Backfill idempotency depends on it.
- `UNIQUE (generation_id, chunk_index)` on `chunks`. Parents and children share one `chunk_index` sequence per generation (T11 must number parent chunks too).
- **Immutability.** Profiles (4 tables): any `UPDATE` raises `restrict_violation`. `DELETE` is not blocked (FKs already protect referenced profiles). `source_versions`: `UPDATE` may change only `duplicate_of` and `upload_state`; changes to `id, source_id, workspace_id, version_number, storage_key, sha256, size_bytes, mime_type, created_by, created_at` raise. `DELETE` is not blocked, so the T20 purge and the `files ON DELETE CASCADE` still work. If T19 needs to fill `sha256`/`size_bytes` after the blob upload, it must insert the row after the bytes are known (or this trigger must be relaxed for `upload_state='uploading'`).
- `upload_state` values: `uploading | stored | failed` (CHECK).
- `source_id` type is `integer` because `files.id` is still `serial`; every new key is uuid. New tables use `timestamptz`.
- `workspace_id` (NOT NULL, indexed, FK to workspaces) is on every workspace-owned new table, including `active_indexes`, `chunk_source_spans`, `retrieval_candidates`, `evidence_items`, `claim_verifications` (needed for T15 policies). `eval_runs`/`eval_results` are global and have none. I added `eval_runs.meta jsonb` (INV-11 model ids and prompt versions) and `id` PKs on `chunk_source_spans` and `retrieval_candidates`.
- History FKs (`retrieval_candidates.chunk_id`, `evidence_items.chunk_id/source_version_id`) are `ON DELETE SET NULL` so purging a source never erases traces/evidence rows.
- Known follow-up: `ingestion_jobs_file_id_unique` (one job per file) still exists and conflicts with job-per-version; T18/T19 must replace it.
- No RLS (T15).

## Backfill

`scripts/backfill-legacy-generations.ts` (not added to package.json by instruction):

```
NEON_DATABASE_URL=<target> npx tsx scripts/backfill-legacy-generations.ts --dry-run
NEON_DATABASE_URL=<target> npx tsx scripts/backfill-legacy-generations.ts --batch-size 50 [--after-id N] [--psql]
```

- Per batch of file ids, one transaction runs 7 set-based statements: version 1, BUILDING generation (`legacy-v1`), chunks from `documents`, `chunk_embeddings_3072` copy, activation (`ACTIVE`, `expected = embedded = count`, only if chunks and embeddings equal the document count), `active_indexes` upsert, `files.desired_version_id/active_version_id`. Every statement is `ON CONFLICT DO NOTHING` or status-guarded, so re-runs and crashes are safe; only unfinished files are selected, so a re-run does 0 batches. `documents` is read-only.
- Mapping: `storage_key` from `files.storage_key`; `mime_type` from `files.type`; `size_bytes` from `files.size`; `created_by` = `files.user_id`; `created_at` = `files.created_at`. `sha256` is computed only for rows still holding the legacy base64 `file_data` (cheap); **blob-backed originals are not fetched, so their sha256 is NULL**. `upload_state`: `uploading` if status `uploading`; `failed` if status `failed` with neither a storage key nor stored bytes; otherwise `stored`. `heading_text` and `token_count` are NULL; `content_hash = sha256(content)`.
- Files without `documents` rows get version 1 and `desired_version_id` but no generation and no `active_version_id`. Soft-deleted files are skipped.
- Final verification (exit code 1 on mismatch): files with documents == legacy ACTIVE generations; documents == chunks == chunk_embeddings_3072 (non-deleted files).
- **Neon branch copy procedure:** (1) create a Neon branch from the production branch; (2) apply 0010 and 0011 to the branch (`psql "$BRANCH_URL" -f drizzle/0010_*.sql -f drizzle/0011_*.sql`); (3) `NEON_DATABASE_URL=$BRANCH_URL ... --dry-run`, then run for real; (4) check the verification block and the 3072 embeddings storage growth against the 0.5 GB free-tier limit (the copy roughly doubles vector storage; D20/T22 drops legacy 3072 vectors later); (5) only then repeat on the production branch. Neon URLs use the `@neondatabase/serverless` HTTP driver (one `sql.transaction` per batch); `localhost` URLs or `--psql` use the `psql` binary.
- The Neon HTTP executor was **not exercised** (no Neon access here); only the psql executor was run. The neon path type-checks and uses the documented `sql.query` / `sql.transaction` API.

## Tests and results

- `npm run lint`, `npm run typecheck` clean; `npm test`: 46 passed, 2 skipped (the existing skipped test plus the new DB test; enable with `TEST_DATABASE_URL`, see file header).
- `TEST_DATABASE_URL=postgresql://scribe:scribe@localhost:54329/t08 npx vitest run __tests__/backfill-legacy-generations.test.ts`: passes (runs inside private schema `t08_backfill_test`).
- `cd intelligence && DATABASE_URL_TEST=... uv run pytest tests/schema -q`: 38 passed against both the previous-schema+migrations DB and the export+migrations DB; 38 skipped without `DATABASE_URL_TEST`.

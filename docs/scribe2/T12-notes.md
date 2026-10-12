# T12 notes: embedding client, indexer, ingest endpoint, `ingest-corpus`

Files: `intelligence/src/scribe_ingest/{embedding,indexer}.py`, `scribe_intel/ingest_routes.py` (+2 lines in `app.py`), `scribe_eval/{ingest_corpus,embed_cache}.py` (+ `ingest-corpus` in `cli.py`), `lib/ingestion/python-engine.ts`, flag in `lib/env.ts` and `lib/ingestion/source-intake.ts`, tests in `intelligence/tests/{ingestion/test_embedding.py,integration/}` and `__tests__/ingest-engine-flag.test.ts`.

## Decisions

1. **Task format (from Google's docs, ai.google.dev/gemini-api/docs/embeddings).** `gemini-embedding-2` does NOT accept `task_type`, so the task goes in a text prefix. Documents: `title: {heading path or "none"} | text: {content}`. Queries: `task: search result | query: {text}`. Implemented as `TaskFormat` (default name `gemini-embedding-2-prefix-v1`, part of the D18 cache key), so it can be swapped without code changes. T13 must embed queries with `embed_queries`, never with the document format. **The seeded profile `gemini-embedding-2-1536` (0011) lists `task_types: RETRIEVAL_DOCUMENT/RETRIEVAL_QUERY`, which contradicts the docs for this model.** The row is immutable and 0011 is not part of T12, so the running code ignores that key. I recommend a later migration that adds a new embedding profile row (new name) recording `task_format` instead. Until then the profile config is documentation only and misleading.
2. **Children-only embeddings (D9).** Parents are stored (context expansion) but not embedded; `expected_chunks` = number of child chunks. The heading path is embedded through the `title:` field and also stored separately in `heading_text`.
3. **Activation order (B3).** One transaction: lock `files` row, check `deleted_at IS NULL` and `desired_version_id`, lock the generation and require `READY` with `embedded = expected`, mark the previous ACTIVE generation SUPERSEDED **before** setting the new one ACTIVE (partial unique index), upsert `active_indexes`, set `files.active_version_id`, `status='ready'`, `text_bytes`, `extracted_text` (the file page preview reads it), delete the superseded generation's embeddings (D20). Refusals raise `ActivationRefused` (`SOURCE_DELETED`, `VERSION_SUPERSEDED`, `NOT_READY`); a build that finds it is stale ends `CANCELLED`.
4. **Failures.** Any failure after the generation row exists marks it `FAILED` with `error_code`; the previous ACTIVE generation is untouched (INV-3). `files.status` becomes `failed` only when the file has no active version. `ingestion_jobs.generation_id/error_code` are updated best-effort; the job `status` belongs to the caller (TS today, T18 later).
5. **Resumability.** Each embed batch is its own transaction; re-running skips chunks that already have embeddings; chunks/blocks are kept when they match. A `TIME_BUDGET` stop leaves the generation `BUILDING` (HTTP 503 retryable). A rerun of an existing non-ACTIVE generation resets it to `BUILDING`; an ACTIVE one is a no-op.
6. **Profile guard.** `load_index_profile` compares the DB chunk profile (child/overlap/parent tokens, tokenizer) with `STRUCTURAL_V1` and raises `PROFILE_MISMATCH` on divergence.
7. **Blocks** are persisted per version once (furniture blocks included, flagged `meta.furniture`); spans are persisted for parents and children.
8. **Endpoint status mapping.** 200 = ACTIVE. 422 = permanent (`INVALID_DOCUMENT`, `UNSUPPORTED_FORMAT`, `OCR_NOT_AVAILABLE`, `NO_STORAGE_URL`, ...). 503 = retryable (provider/DB/storage/time budget). 409 = source deleted or version superseded. 404 = source not in the token's workspace. The error body is `{"detail": {error_code, message, retryable, ...}}`.
9. **TS flag.** `INGEST_ENGINE=python` replaces the extract/chunk/embed block; the catch block only gained `!(error instanceof PermanentIngestError)` so legacy behavior is unchanged. Not mirrored in Python mode: the `embedding_input_tokens` usage event (the response carries no character count; add it in the metering task).

## Known gaps / not verified

* No live Gemini call has been made. The REST body shape (`requests[].model/content/outputDimensionality`) follows the docs; field casing and error JSON are unverified against the live API.
* CSV truncation warnings are returned in the response (`warnings`) but not persisted: `index_generations` has no note column and `error_code` must stay empty on success. Needs a column or a `meta` field (T18/T19).
* Fake and real vectors are indistinguishable in the DB. `ingest-corpus --embedder fake` prints a warning. Always use `--reset` before a Gemini run on the same workspace.
* `ingest-corpus` accepts `--embedder fake` for any database, guarded only by the workspace being kind `eval`. The service refuses fake unless `SCRIBE_ALLOW_FAKE_EMBEDDINGS=1` and the DB name contains `test`.
* **MEASURED 2026-10-12 (PRELIMINARY, 43 corpus passages from .md/.txt files, 20 dev-split queries, 1536 dims, text-prefix task format, script `intelligence/src/scribe_eval/embedding_space_check.py`):** `gemini-embedding-2` and `gemini-embedding-2-preview` returned the same vectors. Cosine of same-text pairs: mean 1.0000, min 0.99999999999999980 (passages) and the same for queries. Cosine of different-text pairs across models: mean 0.614 (min 0.501, max 0.849), so the 1.0 is not a property of the space being generically tight. Top-5 neighbour overlap: 1.00 for model A vs model B rankings, and 1.00 for A-query-on-B-passages. Conclusion: the two ids behave as aliases of one model at 1536 dims, so they share a space (within numerical precision). Caveat: this covers 1536-dim outputs only. The legacy 3072-dim vectors are the preview model at 3072 dims, and a 3072 output is not a 1536 output; whether a truncated 3072 vector equals the 1536 output was NOT tested (Matryoshka truncation plus renormalisation could be checked the same way, but the 3072 vectors would also have been produced with the legacy plain-text format, not the task prefix). Design A2 (keep separate) stays safe; reuse of legacy vectors remains unproven. Rerun: `cd intelligence && set -a; . <env file>; set +a; uv run python -m scribe_eval.embedding_space_check --corpus ../fixtures/corpus --dataset ../benchmarks/datasets/golden-v1`.

## Live ingest

```
cd intelligence
GOOGLE_GENERATIVE_AI_API_KEY=... uv run scribe-eval ingest-corpus --corpus ../fixtures/corpus \
  --profile structural-v1 --database-url postgresql://... --embedder gemini --reset
```

`--database-url` is mandatory (the environment is never consulted). The target workspace must be kind `eval` (owner `eval-golden-v1`, created if missing), and `--reset` deletes only that workspace's rows. Embeddings are cached in `benchmarks/cache/py-embeddings.sqlite` (gitignored; `--cache-path` to change; `SCRIBE_CACHE_MODE=replay` forbids provider calls). Exit code is non-zero unless every document except the scanned PDF is ACTIVE with embedded == expected.

Expected volume for the corpus (from the fake run): 186 child chunks, so 186 texts to embed. Sent as one stream, that is 3 batched requests. The CLI embeds per document, so it makes one request per document with text, 23 requests in all. Another 26 parent chunks are stored but not embedded. Of 24 documents, 23 end ACTIVE and 1 FAILED (`OCR_NOT_AVAILABLE`, expected until T21).

## Local test database

```
psql postgresql://scribe:scribe@localhost:54329/postgres -c 'create database t12_test'
scripts/apply-migrations.sh $U && psql $U -f drizzle/0010_source_versions_generations.sql -f drizzle/0011_seed_profiles.sql
DATABASE_URL_TEST=$U uv run pytest -q
```

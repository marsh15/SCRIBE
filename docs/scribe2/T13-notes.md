# T13 notes: dense + lexical retrieval, snapshot, `POST /retrieve`

Files: `intelligence/src/scribe_intel/retrieval/{queries,dense,lexical,search,registry}.py`, `retrieval/strategies/__init__.py`, `scribe_intel/retrieve_routes.py` (+ router registration in `app.py`), tests in `intelligence/tests/retrieval/{_t13,test_dense,test_lexical,test_scoping,test_snapshot,test_retrieve_endpoint}.py`. `retrieval/types.py` was imported, not edited.

## Strategies (profile names accepted by `/retrieve`)

| name | what | method on candidates |
|---|---|---|
| `dense-exact` (default) | cosine over `chunk_embeddings_1536`, exact | `dense` |
| `dense-ann` | same, HNSW-eligible path (falls back to exact + flag on pgvector < 0.8) | `dense` |
| `lexical-v1` | `fts_en` + `fts_simple`, one list | `lexical` |
| `legacy-dense` | Scribe 1.0 semantics over `chunk_embeddings_3072` | `dense-legacy` |

`dense_mode=auto` exists in `dense_search` (and `run_retrievers`); T14 profiles pick it. There is deliberately no `dense-auto` profile.

## SQL decisions

* **Scoping (INV-2/3/14).** Every statement joins `chunks -> index_generations (status ACTIVE, profile = the profile's index profile, source_version matches) -> active_indexes (same generation, source, profile) -> files (deleted_at IS NULL)`, with `workspace_id = :w` on every table, plus optional `g.source_id = ANY(:src)`. All of it is in JOIN/WHERE before `ORDER BY/LIMIT`. Defence in depth means a mutation test removing only the `status='ACTIVE'` or only `chunks.workspace_id` still passes (the other joins cover it); removing the `deleted_at` filter fails 37 tests.
* **Children only**: `c.parent_id IS NOT NULL` for v2 (parents have no embeddings and are not lexical hits). Legacy chunks are flat (no parents), so no such filter there.
* **Exact (D11).** `ORDER BY (e.embedding <=> q) + 0, c.id`. The `+ 0` makes the ordering expression something the HNSW operator class cannot serve, so the planner can never pick the index; no planner GUC is touched (a GUC like `enable_indexscan=off` would also hurt the btree joins). Verified with EXPLAIN (below).
* **ANN.** `set_config('hnsw.ef_search','100',true)` and `set_config('hnsw.iterative_scan','relaxed_order',true)` (transaction local; test asserts they are back to default after the transaction). Only when `pg_extension.extversion >= 0.8`; otherwise exact + degraded flag `ann_iterative_scan_unavailable`. Shape: `WITH ann AS MATERIALIZED (SELECT chunk_id, dist FROM chunk_embeddings_1536 e WHERE workspace_id = :w AND chunk_id = ANY (ARRAY(<eligible chunk ids>)) ORDER BY e.embedding <=> q LIMIT k) SELECT ... JOIN scope again ORDER BY dist, id`.
  * Why the `ANY(ARRAY(...))`: a scope filter living in a join above the HNSW scan only sees the `ef_search` rows the index returns, so a selective filter silently yields fewer than k rows. As an uncorrelated InitPlan qual on the embeddings scan itself, pgvector's iterative scan applies it while walking the graph. `IN (subquery)` does not work: the planner turns it into a semi-join (verified, plan had HashAggregate and no HNSW).
  * The final re-sort is required because `relaxed_order` may emit slightly out-of-order rows.
  * Cost: the eligible-id array is built once per query (O(active chunks of the workspace)); acceptable at personal-workspace scale, T32 should measure it at >= 20k chunks before `auto` ever picks ann in production.
  * **The planner does not choose HNSW on its own for small tables**; it prefers the pkey / workspace btree. The EXPLAIN test therefore drops `chunk_embeddings_1536_pkey` and the workspace index inside a rolled-back transaction and disables seq/bitmap scans (the "big table" situation): the ann form then uses `chunk_embeddings_1536_hnsw`, the exact form never does, and the real execution through that forced HNSW plan returns exactly the exact-mode top 40 (with and without `source_ids`).
* **auto**: exact when active child chunks of the workspace for the profile < 20,000 (counted with `LIMIT 20000`), else ann.
* **Lexical score (INV-10).** One list, ordered by `greatest(ts_rank_cd(fts_en, qe, 1), ts_rank_cd('{0.4,0.4,0.4,0.4}', fts_simple, qs, 1)) DESC, chunk id`. Both are cover-density ranks of the same ranker family on the same chunk text, so the max compares like with like; nothing is added. `fts_simple` has no `setweight`, so all its lexemes have the default D weight (0.1) versus B (0.4) for English body text; the weights array equalises body-level matches. A chunk is a hit if either tsvector matches. Normalization flag 1.
* **Stopword-only queries** (`the`, `the of and a`) return `[]`: the English tsquery is empty so the statement is guarded with `(select numnode(qe)) > 0`; without it the `simple` config (no stopwords) would match any chunk containing "the". Empty/blank/NUL-only also `[]`.
* **Query text is only ever a bound parameter.** `clean_query` strips NULs (Postgres rejects them) and caps at 2000 chars. Hostile strings (quotes, `;drop table`, tsquery operators, `%s`, unicode) are tested.
* **Legacy-dense.** `1 - (embedding <=> q) > threshold` (strict, default 0.3), `ORDER BY similarity DESC`, `LIMIT limit` (default 10), exact only, same workspace/ACTIVE/not-deleted scoping against the `legacy-v1` index profile. Query embedding for legacy: `gemini-embedding-2-preview`, 3072 dims, raw text with no task prefix (`TaskFormat` `legacy-none-v1`), like `lib/embeddings.ts` (apart from whitespace collapsing, which is not reproduced). Verified on a seeded 3072-d fixture (exact cosines, magnitude-independent, 0.3001 passes / 0.2999 fails, 14 above threshold -> 10 returned).

## Identifier tokenization finding (pg17, `simple` config)

`ts_debug`/`to_tsvector('simple', ...)`:

| text | lexemes (position) |
|---|---|
| `ERR_AUTH_403` | `err`:1 `auth`:2 `403`:3 (underscores split) |
| `INV-2047` | `inv`:1 `-2047`:2 (hyphen + digits is a **signed integer** token) |
| `NW-COLD-04` | `nw-cold`:1 `nw`:2 `cold`:3 `04`:4 (compound + parts) |
| `v4.2.1` | `v4.2.1` (one `file` token) |

`websearch_to_tsquery('simple', 'ERR_AUTH_403')` = `'err' <-> 'auth' <-> '403'`, `INV-2047` = `'inv' <-> '-2047'`, `NW-COLD-04` = `'nw-cold' <-> 'nw' <-> 'cold' <-> '04'`. The query parser is the same as the document parser and joins the pieces with `<->`, so an identifier typed the way it is stored already matches as a phrase; **no trigram or ILIKE is needed** (D10). The corpus identifiers `ERR_AUTH_403`, `INV-2047`, `NW-COLD-04` rank the correct chunk in the top 3 on the full ingested corpus (tested).

What does not match, and what was done:
* Other separator spelling: stored `INV-2047` vs query `INV_2047` (`'inv' <-> '2047'` vs stored `-2047`), or the reverse. Fixed by `separator_variant`: if the query contains a token like `A-B`/`A_B` the whole query is also run with hyphen <-> underscore swapped, and the two tsqueries are OR-ed (`websearch_to_tsquery(q) || websearch_to_tsquery(q')`) for both columns. Tested both directions.
* A bare number (`2047`) does **not** hit `INV-2047` (stored lexeme is `-2047`). Not fixed (known gap, asserted in a test so a change is noticed). A leading `-` in a query is websearch negation (`-2047` = `!'2047'`).
* `pg_trgm` was not needed.

## Snapshot / latency design

`snapshot_tx(pool, wid)`: pooled autocommit connection, `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`, then `set_config('app.workspace_id', wid, true)` as the first statement (this is what fixes the snapshot), `ROLLBACK` on exit. The caller starts the query-embedding task BEFORE entering the snapshot (so connection checkout overlaps the provider call); inside, `run_retrievers` runs `gather(lexical, embedding)` on the single connection (lexical is DB-bound, embedding is network-bound), then dense with the vector, then one batched span hydration. The snapshot is therefore held for max(embed, lexical) + dense + hydrate, not their sum, and both retrievers see the same active generations (test: another connection activates v2 mid-snapshot, the snapshot still returns v1 from both retrievers, a fresh snapshot returns v2; same for soft delete). `gather(..., return_exceptions=True)` so nothing is left running on the connection when one side fails. An embedding failure propagates (no lexical-only fallback) -> 503.

`run_retrievers` signature as specified, with `query_vec` accepting a vector or an awaitable; `dense_k=0` / `lexical_k=0` skip a retriever (used by the single-method strategies). `RetrievalSnapshot` has `dense, lexical, degraded, stage_ms (embed, lexical_plus_embed_wait, dense, hydrate, total), counts, dense_mode`. Spans: one query `chunk_source_spans JOIN document_blocks` for all candidate ids (page/bbox coalesced from span then block); `doc_name` comes from the main query's `files` join.

## `POST /retrieve`

Body `{query (1..2000, non-blank), profile?='dense-exact', source_ids?[int] (<= 500), limit? 1..50 = 10}`; extra fields (e.g. `workspace_id`) ignored; scope `retrieve`; workspace from the token. Response: `{profile, trace_id, degraded[], candidates[], stage_ms, counts, meta}`; each candidate has `rank, chunk_id, parent_id, doc_name, heading_text, content, method, score, dense_/lexical_/fusion_/rerank_ rank+score, source_id, source_version_id, generation_id, content_hash, token_count, spans[{block_id, block_index, char_start, char_end, page, bbox, ocr, row_number}], provenance{...}`. Errors are top-level JSON `{error_code, message, retryable}` (not nested under `detail`, unlike `/ingest/source`): 422 validation (FastAPI default body), 404 `UNKNOWN_PROFILE` (lists profiles), 503 `RETRIEVAL_FAILED` (embedding provider or database error, with `cause` and `trace_id`; the message never contains query text), 500 `FAKE_EMBEDDER_REFUSED`. Trace: `retrieval_traces` (status ok/failed, `degraded` comma-joined, `stage_ms`, `counts` incl. profile name and `returned`, `config_hash` = sha256 of profile/limit/source_ids; `profile_id` stays NULL because strategies are not `retrieval_profiles` rows yet) and `retrieval_candidates` for the whole ranked list (the first `limit` flagged `selected`), written in a separate short transaction after the snapshot closed. Persist failure -> 200 + degraded `trace_persist_failed`, log line without text.

## What T14 must register / consume

* Add modules under `retrieval/strategies/` (auto-imported by `registry.discover_strategies()`); register with `@register("hybrid-a")` an `async def (ctx: RetrieveContext) -> StrategyResult`. `ctx` has `pool, workspace_id, query, source_ids, limit, embedder_for(dims)`.
* Typical body: start `embedder_for(1536).embed_queries([q])` as a task, `async with snapshot_tx(ctx.pool, ctx.workspace_id) as conn:` `prof = await resolve_index_profile(conn, 'structural-v1')`, `snap = await run_retrievers(conn, wid, q, query_vec=task, index_profile_id=prof, source_ids=..., dense_k=40, lexical_k=40, dense_mode='auto')`, then fuse `snap.dense`/`snap.lexical` (ranks only, INV-10), rerank, etc. outside the transaction; return `StrategyResult(ranked=[RankedCandidate...], stage_ms=snap.stage_ms | {...}, counts, degraded=snap.degraded + ['rerank_unavailable'?], extra)`. Extra fields on `RankedCandidate` (fusion_rank, rerank_*) are serialised and traced automatically; `selected` is set by the route from `limit`.
* The route returns the first `limit` of `ranked`; if T14 needs a different notion of "returned" (packing), add a field to `StrategyResult` rather than editing the route's candidate loop.
* `retrieval_profiles` A-E rows (T14 seeds) can fill `retrieval_traces.profile_id`; the route currently leaves it NULL.
* Embedding profile caveat (T12): the code ignores the seeded profile's `task_types`; the query format is `task: search result | query: {text}`.

## Results and unverified items

* Tests: `tests/retrieval/` T13 files only; run `cd intelligence && DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/t13_test uv run pytest -q` (all skip without `DATABASE_URL_TEST`). The golden-v1 questions (120) all run through `lexical-v1`, `dense-exact`, `dense-ann` on the fully ingested corpus with the fake embedder; **no quality metric is reported (fake vectors have no meaning)**.
* Not verified: Neon's pgvector version (T08 note stands; `ann` degrades to exact + flag below 0.8); a live Gemini query embedding (query format/legacy 3072 query embedding unverified against the live API); HNSW behaviour and the eligible-id-array cost at >= 20k chunks (no large-scale data here); `hnsw.iterative_scan` effect on recall with highly selective filters (only functional equality with exact was tested, on tiny data); behaviour behind Neon's PgBouncer pooler for `BEGIN ISOLATION LEVEL ...` as an explicit statement (transaction pooling keeps one backend for the transaction, which should be fine, but it was not tried); the 3072-d legacy query path with the real `gemini-embedding-2-preview` and whitespace collapsing as in `lib/embeddings.ts`.

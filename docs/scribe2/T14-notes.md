# T14 notes: fusion, rerank, dedup, packing, profiles, pipeline (DB-free half)

Code: `intelligence/src/scribe_intel/retrieval/{fusion,rerank,dedup,packing,profiles,pipeline}.py`.
Tests: `intelligence/tests/retrieval/test_{rrf,rerank,dedup,packing,profiles,pipeline}.py` (+ `_t14_factories.py`).
Everything is offline: Voyage is exercised via `httpx.MockTransport`; no key is needed.

## Fusion (INV-10)
`score = sum 1/(k+rank)`, ranks only (`Candidate.rank`; same-rank ties inside a list are ordered by chunk id,
so list input order is irrelevant). Tie-break: more lists, best single rank, `str(chunk_id)`. Scores compared
as exact Fractions. Both-lists property: if X is in both lists and Y only in list L with rank_L(X) <= rank_L(Y),
X is fused strictly above Y (score(X) > 1/(k+rank_L(X)) >= score(Y)). Tested with Hypothesis.

## Voyage facts encoded
POST `https://api.voyageai.com/v1/rerank`, `Authorization: Bearer`; body `query, documents, model, top_k,
return_documents(false), truncation(true, default)`; response `data[{index, relevance_score}]`, `usage.total_tokens`.
Limits (rerank-2.5): <= 1000 docs/request; query <= 8,000 tokens; query + one doc <= 32,000; query_tokens*docs
+ doc tokens <= 600K. We send <= 20 docs, each capped at 2048 tokens (`max_doc_tokens`), `truncation=true` as backup.
Retries (4) on 429/5xx/network with Retry-After (capped 30 s); 401/403 AUTH and other 4xx INVALID_REQUEST are not retried.

## Decisions for the lead
1. Profiles A-E are my choice (`dense-only`, `lexical-only`, `hybrid-rrf`, `dense-rerank`, `hybrid-rrf-rerank`); section 40.1 was unavailable. Seed ids `a0000000-0000-4000-8000-00000000000{1..5}`.
2. `config_hash` = sha256 of Python canonical JSON (sorted keys, compact). It differs from the `sha256(jsonb::text)` formula used by the SQL seeds in 0011; `upsert_profiles` verifies stored hash and re-hashed stored config. `rerank_model` is in the config, so changing it needs a new profile. `upsert_profiles` expects a sync psycopg connection.
3. Single-list profiles (A, B, D) still pass through `rrf_fuse` (order preserved; gives `fusion_rank`).
4. Rerank output = reranked head first (rerank_rank 1..n), then the un-reranked tail in fused order. Only the head is context-eligible when reranking succeeded; when degraded the first `rerank_top_n` fused candidates are used (pipeline).
5. Degraded labels: `rerank_unavailable` (any `RerankError`, invalid indices, or no reranker supplied for a rerank profile) and `parent_expansion_unavailable` (loader raised). `CacheMiss` (replay mode) is NOT degraded; it propagates loudly (`/retrieve`: HTTP 500 `RERANK_CACHE_MISS`, `retryable=false`). On any degrade the provider error code is recorded as `rerank_error` (response `meta` and trace `counts`) and logged once as a warning; the log carries the code only, never the key, query or document text.
6. Cache: `CachedReranker` caches the FULL ranking of the docs (inner asked for all docs), so the D18 key `(model, query, ordered doc hashes)` needs no top_n; the key is byte-identical to `scribe_eval.cache.rerank_key` (tested). Works with `DiskCache.get_or_compute` or duck-typed `get/put`; `CacheMiss` is imported from `scribe_eval.cache` (falls back to a local equivalent).
7. Dedup policy: see `dedup.py` docstring. Hash duplicates keep the best-ranked one with `duplicates`/`also_in`; one child per parent only when `expand_parents` is on. If a parent doesn't load/fit, merged siblings are re-emitted as separate entries (no evidence silently lost).
8. Parent expansion: parent replaces child only if <= 50% of the budget and it fits the running total. Child spans (unioned with merged siblings) are kept for citation. `parent_loader(list[UUID]) -> {parent_id: str | ParentContent(content, heading_text)}`.
9. Packing stops at the FIRST overflow (no skip-and-fill); dropped list has reasons `exceeds_budget` / `after_budget_stop`. Token cost = text + heading tokens. `PackedItem` was sufficient; provenance lives in `PackedEntry`/`PackedContext` (packing.py). `types.py` untouched.
10. `PipelineResult.packed` is a `PackedContext`; `RankedCandidate.selected` is set for chunks that reached the context (including siblings merged into an expanded parent, and hash duplicates of a packed chunk: `PackedEntry.duplicates`, which dedup folded into the kept chunk's text).
11. Trace linking: `config_hash` of a seeded profile's trace is sha256 of `{"profile": <profile config_hash>, "limit", "source_ids"}` (helper `trace_config_hash`), identical for ok and failed traces of one request (the profile is looked up in `SEED_PROFILES` by the requested name). `retrieval_traces.profile_id` is resolved by `name AND config_hash`, so a same-name row with a different config is never linked.
12. Migration 0012 seeds with `ON CONFLICT DO NOTHING`, then a `DO` block RAISES if any seeded id/name already exists with a different `config` (jsonb comparison, key order irrelevant) or `config_hash`; the seed values live once in a temp table dropped at the end.

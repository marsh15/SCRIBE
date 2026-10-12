# Scribe 2.0 performance report (T32)

This report covers scale, speed, ANN accuracy and storage of the dense and lexical retrievers as the corpus grows.
The tool is `scribe-perf` (`intelligence/src/scribe_perf`). Raw results: `benchmarks/perf/results-m1-orbstack.json` (about 63 KB).

READ THIS FIRST. These numbers come from a Mac laptop running Postgres in an OrbStack Docker VM, with SYNTHETIC
vectors and text. They are not Neon, not Vercel, and not real embeddings. Absolute milliseconds will differ in
production, so use the numbers for shape (how cost grows, which settings matter), not as promises. The laptop was shared with other activity during the runs. Free disk swung between 2.8 and 12 GB while the database size stayed constant, and CPU contention was not measured, so several p95/p99 values are noisy (see Caveats).

## 1. Findings

1. Exact dense search costs about 14 microseconds per eligible chunk here and grows linearly: p50 9.9 ms at 700
   chunks, 79 ms at 7,000, 300 ms at 21,000 (workspace sizes, vector(1536), k=40).
2. The shipped `ann` SQL did NOT use the HNSW index in any measured case (0 of 15 workspace/scale combinations).
   The planner chose the primary-key lookup over the eligible-id array and sorted those rows. That plan is still faster
   than exact for workspaces of 700 chunks or more (1.6x to 1.7x at 2,000 to 21,000 chunks, 1.3x to 1.5x at 700). It returned recall@10 = 1.0 in
   every case, because it is effectively exact over the eligible set. Today, then, `auto` above 20k gives a modest speed-up
   rather than an index lookup. Whether the planner picks HNSW at larger table sizes was NOT measured (see section 3).
3. When HNSW is forced (pkey and workspace btree dropped inside a rolled-back transaction, the same trick as
   `tests/retrieval/test_dense.py`), it is 3x to 3.4x faster than exact for a workspace that is most of the table
   (21,000 of 30,000 chunks: p50 100 ms vs 300 ms, recall@10 0.99). It is SLOWER than exact for a workspace that is a small
   share of a large index (300 of 30,000 chunks: 78 ms vs 7 ms). The crossover depends on the workspace's share of the
   whole table, not on its chunk count alone.
4. `hnsw.iterative_scan` matters. With it off, a selective workspace filter silently returns fewer than k rows
   (30,000-chunk table, 300-chunk workspace, ef_search=100: 16.5 of 40 rows, recall@10 0.84). With `relaxed_order`
   the rows are always 40 and recall@10 is 0.99 to 1.0, at the cost of a slower scan (78 ms vs 35 ms for that workspace).
   recall@40 with `relaxed_order` stays below 1 for small workspaces (0.86 to 0.91 at 30,000 chunks). The cause was not isolated;
   a hypothesis is that the default `hnsw.max_scan_tuples` of 20,000 is hit before the whole selective set is seen.
5. halfvec(1536) halves storage (3,076 vs 6,148 bytes per embedding, HNSW 4.1 KB vs 8.2 KB per embedding) with a small
   accuracy cost on this data: exact-halfvec vs exact-vector recall@10 0.999 to 1.0, recall@40 0.9995 to 1.0 at the largest workspaces.
6. `AUTO_ANN_THRESHOLD` stays 20,000 (unchanged). The reasoning is in section 10.
7. The 100,000-chunk step loaded (404 s), but its measurement phase was ABORTED because free disk on the
   Mac fell to 2.8 GB. No 100k latency or recall numbers exist. See section 3.

## 2. Environment

| item | value |
|---|---|
| machine | MacBookAir10,1, Apple M1, 8 CPUs, 8 GiB RAM (`sysctl machdep.cpu.brand_string hw.ncpu hw.memsize`) |
| macOS | 27.0 (`sw_vers -productVersion`) |
| Docker | OrbStack, Docker server 29.4.0, VM limits seen by `docker info`: 8 CPUs, 3.893 GiB memory |
| Postgres | 17.11 (Debian, aarch64), container `scribe-db-bridge` on localhost:54329 |
| pgvector | 0.8.7 (iterative scan supported) |
| Postgres settings (defaults) | shared_buffers 128MB, work_mem 4MB, effective_cache_size 4GB, max_parallel_workers_per_gather 2, max_connections 100. `maintenance_work_mem` raised to 512MB for the index builds only |
| HNSW | m=16, ef_construction=64 (same as `drizzle/0010`), `vector_cosine_ops`; halfvec index identical parameters with `halfvec_cosine_ops` |
| client | one Python 3.12 process, psycopg 3, same Mac (no network hop) |
| production target (not measured) | Neon + Vercel Hobby |

Database: a throwaway `perf_t32_dev` created by the tool, schema replayed with `scripts/apply-migrations.sh`
(drizzle export plus migrations 0010+), dropped after use. No existing database (`t13_test` etc.) holds perf data.

## 3. Scale steps actually run

| scale (child chunks) | ran? | free disk GB before / after load / after measure | load + index build |
|---|---|---|---|
| 1,000 | yes | 12.14 / 12.14 / 12.29 | 3.3 s |
| 10,000 | yes | 12.29 / 12.16 / 12.05 | 36.1 s |
| 30,000 (EXTRA step, added to bracket the 20k threshold) | yes | 12.05 / 11.41 / 11.46 | 146.0 s |
| 100,000 | load only, measurement aborted | 11.41 before; 6.16 after load; fell to 2.8 while measuring | load + index 403.8 s |

The 100,000 step passed the 8 GB-free gate (11.41 GB) and loaded. During measurement, free disk on the Mac dropped
from 6.2 GB to 2.8 GB while the database itself stayed stable (`du` in the container: base 2.9 GB plus WAL 1.1 GB), so
something else on the machine was consuming disk. The run was killed and the database dropped (`dropped; exists=False`). The 100k
run wrote no result file. Observed (not extrapolated): at 100,000 chunks the database directory was about 2.9 GB.

ESTIMATE (linear extrapolation of the measured per-chunk bytes in section 7, not measured at 100k): vector table 0.84 GB,
vector HNSW 0.82 GB, halfvec table 0.42 GB, halfvec HNSW 0.41 GB. Build time at 100k is unknown. The 30k vector HNSW build took 40 s, but
at 100k the graph exceeds the 512 MB `maintenance_work_mem`, so build time will not scale linearly.

Each scale has 5 workspaces holding 70%, 20%, 7%, 2% and 1% of the chunks, so "ws0" is a large workspace
and "ws4" a selective 1% one. Workspace sizes below are the chunk counts of that workspace.

## 4. Method

* Data (seed 32, all deterministic): topics (shared by all workspaces), then documents (10 to 30 chunks each), then chunks. Each level is a
  normalized Gaussian perturbation of its parent (`synth.py`: DOC_NOISE 0.9, CHUNK_NOISE 0.7). Vectors are unit length (checked in the tests).
  Text: 60 words per chunk drawn from a seeded 3,000-word pseudo vocabulary with Zipf weights (exponent 1.1), a 3-word heading.
  One parent chunk per document (not embedded, like production); only child chunks carry embeddings.
* Rows: workspaces, files, source versions, ACTIVE generations, active_indexes, chunks (generated `fts_en`/`fts_simple` columns
  computed by Postgres), `chunk_embeddings_1536`, plus a perf-only `chunk_embeddings_1536_half` (halfvec) copy with its own HNSW index.
* Queries: 100 per workspace at 1k, 10k and 30k (cycled when a workspace has fewer documents). Half are document-level
  (a perturbed document centre), half topic-level (a perturbed topic centre, no single dominant document).
  Lexical queries: 1 to 3 pseudo-words from frequency ranks 20 to 1500.
* SQL: the production builders `dense_sql(...)` and `lexical_search(...)` from `scribe_intel.retrieval`, k=40, full scope joins (generation ACTIVE,
  active_indexes, files not deleted, workspace on every table). Only `hnsw.ef_search` and `hnsw.iterative_scan` are varied, as transaction-local
  `set_config` calls included in the timed region. 5 warm-up queries are discarded; each query is then timed once. Percentiles are nearest-rank.
* Ground truth for recall: the exact (`ORDER BY dist + 0`) vector result for the same query. recall@10 = |top10(exact) intersect top10(path)| / 10, averaged over queries; recall@40 likewise.
  halfvec "recall" is therefore halfvec vs vector ground truth.
* Paths: `exact` (production exact SQL); `ann as planned` (production ann SQL, ef_search 100, relaxed_order, whatever plan the planner picks;
  `uses_hnsw` recorded from `EXPLAIN`); `HNSW forced` (inside a transaction that is rolled back: pkey and workspace btree of both embedding
  tables dropped, `enable_seqscan` and `enable_bitmapscan` off, so the ann SQL must walk HNSW; verified with `EXPLAIN`, `uses_hnsw` true in the JSON).
* Eligible-id array: the production ann SQL builds `ARRAY(select c.id ...)` of every eligible chunk (an InitPlan) so pgvector's iterative scan can apply the
  scope. It is timed on its own ("eligible-id array build").
* Concurrency: N async client tasks (1, 10, 50), each borrowing a pooled connection per query and calling the production `dense_search`; about 200 queries per level;
  `auto` at every scale, plus forced `exact` and `ann` at the last scale. The client is a single Python process on the same machine, so it also competes for CPU.

## 5. Latency and accuracy (large workspace, k=40, 100 queries)

Exact vs ANN as planned vs HNSW forced (ef_search 100, relaxed_order):

| scale | ws0 chunks | exact p50 / p95 / p99 ms | ann as planned p50 / p95 ms (uses HNSW) | HNSW forced p50 / p95 ms | forced recall@10 / @40 |
|---|---|---|---|---|---|
| 1k | 700 | 9.9 / 10.6 / 11.0 | 6.6 / 7.0 (no) | 4.4 / 5.1 | 1.00 / 1.00 |
| 10k | 7,000 | 79.1 / 137.9 / 215.7 | 45.5 / 51.0 (no) | 23.1 / 44.9 | 1.00 / 1.00 |
| 30k | 21,000 | 300.3 / 369.9 / 429.7 | 189.1 / 218.4 (no) | 100.4 / 113.4 | 0.99 / 0.989 |

Crossover table across every workspace of every scale (sorted by workspace size; full columns in the appendix):

| workspace chunks | share of table | exact p50 ms | ann as planned p50 ms | HNSW forced p50 ms | forced recall@10 |
|---|---|---|---|---|---|
| 10 | 1% | 2.8 | 3.0 | 7.0 | 1.00 |
| 100 | 1% | 4.7 | 4.7 | 25.6 | 1.00 |
| 300 | 1% | 7.1 | 6.5 | 77.7 | 1.00 |
| 700 | 70% | 9.9 | 6.6 | 4.4 | 1.00 |
| 700 | 7% | 11.9 | 9.4 | 9.8 | 0.997 |
| 2,000 | 20% | 26.2 | 16.7 | 7.0 | 0.999 |
| 2,100 | 7% | 27.1 | 17.4 | 44.7 | 0.986 |
| 6,000 | 20% | 68.7 | 40.7 | 44.0 | 0.993 |
| 7,000 | 70% | 79.1 | 45.5 | 23.1 | 1.00 |
| 21,000 | 70% | 300.3 | 189.1 | 100.4 | 0.99 |

ef_search and iterative_scan on the selective workspaces (30,000-chunk table, recall@10 / rows returned of 40; HNSW forced; latency p50 ms):

| workspace (chunks, share) | iterative_scan | ef 40 | ef 100 | ef 200 |
|---|---|---|---|---|
| ws4 (300, 1%) | off | 0.67 / 14.0 rows / 35 ms | 0.84 / 16.5 / 35 ms | 0.965 / 21.2 / 35 ms |
| ws4 (300, 1%) | relaxed_order | 1.00 / 40 / 73 ms | 1.00 / 40 / 78 ms | 1.00 / 40 / 74 ms |
| ws1 (6,000, 20%) | off | 0.832 / 17.3 / 49 ms | 0.983 / 28.7 / 46 ms | 0.993 / 35.7 / 43 ms |
| ws1 (6,000, 20%) | relaxed_order | 0.988 / 40 / 49 ms | 0.993 / 40 / 44 ms | 0.993 / 40 / 45 ms |
| ws0 (21,000, 70%) | off | 0.969 / 31.5 / 113 ms | 0.99 / 39.6 / 99 ms | 1.00 / 40.0 / 104 ms |
| ws0 (21,000, 70%) | relaxed_order | 0.984 / 40 / 100 ms | 0.99 / 40 / 100 ms | 1.00 / 40 / 101 ms |

Reading: the default ef_search=100 with relaxed_order is a sound setting on this data. `off` is unsafe for workspace-scoped queries because it silently returns too few rows. Going to
ef_search=200 added accuracy at 70% share, with no latency penalty that stood out from noise. The 1,000 and 10,000 scale tables are in the appendix.

## 6. Lexical (Postgres FTS, `fts_en` + `fts_simple`, k=40)

| scale | workspace chunks | p50 / p95 / p99 ms | mean hits |
|---|---|---|---|
| 1k | 700 | 2.9 / 4.2 / 5.7 | 3.5 |
| 1k | 10 | 1.8 / 3.0 / 4.8 | 0.07 |
| 10k | 7,000 | 2.9 / 12.9 / 16.1 | 13.0 |
| 10k | 100 | 2.3 / 3.2 / 3.7 | 0.8 |
| 30k | 21,000 | 3.6 / 32.6 / 40.4 | 14.5 |
| 30k | 300 | 2.5 / 2.9 / 3.0 | 1.9 |

Lexical p50 is flat (a GIN lookup). The tail grows with the hit count, because every matching chunk is ranked with `ts_rank_cd` before the top 40 are taken.
The text is synthetic Zipf text, so real term distributions will behave differently.

## 7. Storage per chunk (measured, `pg_column_size`, `pg_table_size`, `pg_relation_size`)

| item | vector(1536) | halfvec(1536) |
|---|---|---|
| embedding value (avg `pg_column_size`) | 6,148 B | 3,076 B |
| embeddings table incl. TOAST per embedding (10k / 30k scale) | 8,395 / 8,391 B | 4,248 / 4,229 B |
| HNSW index per embedding | 8,193 B | 4,096 B |
| vector + HNSW per chunk | about 16.6 KB | about 8.3 KB |
| HNSW build time (10k / 30k) | 10.6 s / 40.1 s | 4.9 s / 18.9 s |

Other per-chunk bytes (synthetic 60-word text, including the two generated tsvector columns): the `chunks` table with its indexes takes 2.9 to 4.8 KB per chunk (the scale-dependent part is
fixed index overhead at small scale), GIN `fts_en` about 0.3 to 1.0 KB, and GIN `fts_simple` about 0.3 to 1.0 KB. The whole database at 30k chunks is 850 MB. The 8.4 KB
table row for a 6.1 KB vector arises because values over about 2 KB are TOASTed.
ESTIMATE (linear, from the above): one million chunk embeddings would take about 16.6 GB as vector + HNSW and about 8.3 GB as halfvec + HNSW. Not measured.

## 8. Concurrency (`auto`, main workspace, single Python client process)

| scale | clients | qps | p50 / p95 / p99 ms | mode used |
|---|---|---|---|---|
| 1k | 1 | 63.7 | 14.2 / 20.1 / 40.0 | exact |
| 1k | 10 | 146.1 | 65.3 / 94.9 / 108 | exact |
| 1k | 50 | 128.8 | 368 / 532 / 567 | exact |
| 10k | 1 | 10.4 | 89.4 / 128.2 / 184 | exact |
| 10k | 10 | 12.4 | 638 / 1450 / 1746 | exact |
| 10k | 50 | 13.6 | 3599 / 4455 / 4828 | exact |
| 30k | 1 | 4.5 | 201 / 343 / 553 | ann |
| 30k | 10 | 11.7 | 836 / 1050 / 1102 | ann |
| 30k | 50 | 11.0 | 4424 / 5276 / 5419 | ann |

Same 30k step, forced `exact`: 3.1 / 5.5 / 5.1 qps and p50 302 / 1791 / 9214 ms; forced `ann`: 3.6 / 10.0 / 10.7 qps and p50 197 / 861 / 4583 ms (1 / 10 / 50 clients).
Throughput saturates near 10 clients. The dense query is CPU-bound in one Postgres backend per query, the VM has 8 vCPUs, and Postgres parallel workers per query were 2,
so 50 concurrent users mostly queue (latency grows about linearly with clients).
With one Neon compute and a Vercel function per request this will behave differently. The transferable part is the shape: saturation near the core count, and exact being about 2x worse than ann under load.

## 9. Cost (documented facts only; no per-query prices)

* Embedding calls at ingestion: only child chunks are embedded. `scribe_ingest.embedding.BATCH_SIZE = 100` texts per `batchEmbedContents` request, so a document with
  n child chunks costs `ceil(n / 100)` embedding requests (a typical 10 to 30 chunk document: 1 request; 100,000 chunks: 1,000 requests). Cached texts (D18, keyed by model, dims, task, sha256) cost 0 requests.
* Per retrieval query: 1 query-embedding request (`embed_queries`); profiles with rerank (dense-rerank, hybrid-rrf-rerank, the default) add 1 rerank request (`rerank-2.5`, top 20); profiles without rerank add 0.
  Lexical search makes no provider call. Query embeddings are cached the same way (D18).
* Storage (Neon free tier is the stated constraint in D20): section 7. halfvec halves vector and HNSW bytes.
* Compute: section 5/8 (CPU-bound, linear in eligible chunks for exact). No dollar figures are given because no live prices were consulted.

## 10. `auto` threshold (D11: exact below 20,000 child chunks per workspace)

Decision: `AUTO_ANN_THRESHOLD` stays 20,000, with no code change. It is a module constant in `intelligence/src/scribe_intel/retrieval/dense.py`, outside the hashed
profile config (`ProfileConfig` and `config_hash` carry no dense mode or threshold; the mode actually used is recorded on the snapshot and in the trace), so changing it would not break profile immutability.
`tests/retrieval/test_dense.py` imports the constant and monkeypatches it, and no test pins the value 20,000.

Why not change it, based on this Mac/OrbStack data (not Neon or Vercel, so results do not transfer one-to-one):

* There is no single crossover. `HNSW forced` beat exact from about 700 to 2,000 chunks when the workspace was 20% or more of the table, tied at 7% share and about 700 chunks,
  and lost by up to 11x for a 1% workspace (300 chunks: 78 vs 7 ms). A per-workspace chunk-count threshold cannot encode this, and Personal workspaces (D11's rationale) are usually a small share of a shared table.
* The production ann SQL did not use HNSW in any case measured, so lowering the threshold would only switch to the faster primary-key plan (1.6 to 1.7x over exact for 2,000+ chunks, recall 1.0), not to an index scan.
* At and just above the current threshold (21,000 chunks, 70% share): exact p50 300 ms, ann as planned 189 ms, HNSW forced 100 ms with recall@10 0.99. If a dense latency budget below about 300 ms is needed at 20k,
  that is the point to revisit, with data from Neon itself.
* The regime `auto` targets (>= 20k per workspace, big shared table) was measured only up to 21,000 per workspace and 30,000 total. 100k was not measured, so the planner's choice there is unknown.

Follow-ups worth doing (not done here):

- Profile why exact costs about 14 microseconds per chunk. TOAST fetches and the 4-table scope join are candidates, but this was NOT profiled.
- Check `hnsw.max_scan_tuples` against the recall@40 shortfall.
- Rerun on Neon.

## 11. Commands

```
cd intelligence
export PATH=/opt/homebrew/bin:$PATH
# scale steps 1k, 10k, 30k (what produced results-m1-orbstack.json):
uv run scribe-perf run --database-url postgresql://scribe:scribe@localhost:54329/perf_t32_dev \
  --scales 1000,10000,30000 --seed 32 --queries 100 --out ../benchmarks/perf/results-m1-orbstack.json
# 100k attempt (aborted during measurement, no result file):
uv run scribe-perf run --database-url postgresql://scribe:scribe@localhost:54329/perf_t32_dev \
  --scales 100000 --seed 32 --queries 50 --out ../benchmarks/perf/results-m1-orbstack-100k.json --drop
uv run scribe-perf render ../benchmarks/perf/results-m1-orbstack.json   # prints the appendix tables
uv run scribe-perf drop --database-url postgresql://scribe:scribe@localhost:54329/perf_t32_dev
```

The tool refuses any database whose name does not start with `perf_` (or `t32_test_`). The run skips a step automatically if free disk is below 2 GB (8 GB for scales of 100,000 and above) and drops the database after a step when free disk is under 6 GB.
Tests: `intelligence/tests/perf` (latency stats, recall, loader and measurement helpers at 200 rows in a throwaway `perf_test_*` database that the test creates and drops).

## 12. Caveats and unverified

* The vectors are synthetic, clustered unit vectors with seeded Gaussian noise. Recall on real embeddings (lower intrinsic dimension, different neighbor structure) will differ, and the 1.0 recalls here may be optimistic.
* This is one machine and one run per configuration, on a laptop with other workloads (disk pressure; CPU contention is possible but was not measured). Single-run p95/p99 are therefore noisy: for example, one 30k default-config measurement in an earlier discarded run showed a p99 of 1.5 s that did not recur.
  Medians are the more trustworthy column. There are no confidence intervals.
* Not Neon, which differs in storage, cache, autovacuum, compute size, pgvector build and connection pooler. Not Vercel either.
* The "HNSW forced" mode is an experimental condition (indexes dropped inside a rolled-back transaction); it shows HNSW's own behaviour, not what production would plan.
* RLS policies exist in the schema, but the perf role is the table owner, so the policies are not applied. Every query carries the explicit workspace predicate, as in production. RLS overhead is therefore not measured.
* Postgres and OS caches were warm (5 discarded warm-up queries), and cold-cache latency was not measured. The embeddings table (about 0.25 GB at 30k) fit in the VM's page cache.
* 100,000 chunks: loaded, not measured (section 3). Storage at 100k and 1M are ESTIMATES. `hnsw.max_scan_tuples` was not read back from the server in the JSON (shown as null), so the pgvector default of 20,000 is assumed.
* An earlier full run (same code, per-workspace topics, no forced-HNSW section) was discarded and overwritten; it showed the same planner behaviour (HNSW not used).

## Appendix: raw tables (generated by `scribe-perf render`)

### Scale 1000 chunks
Load+index 3.3 s; build s: {'halfvec_table_s': 0.042, 'hnsw_halfvec_s': 0.357, 'hnsw_vector_s': 0.694}; free disk GB before/after load/after measure: 12.14/12.14/12.29

| metric | vector(1536) | halfvec(1536) |
|---|---|---|
| avg embedding column bytes | 6148.0 | 3076.0 |
| table heap bytes / embedding | 8478.7 | 4292.6 |
| HNSW bytes / embedding | 8200.2 | 4104.2 |
| HNSW index total bytes | 8200192 | 4104192 |

**ws0 (700 chunks, 100 queries; eligible-id array build p50 2.446 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 9.921 | 10.588 | 11.007 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 8.433 | 8.61 | 8.854 | 1.0 | 0.9995 | - |
| ann as planned (vector) | 100 | relaxed_order | 6.559 | 6.978 | 7.207 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 4.274 | 4.755 | 5.276 | 1.0 | 0.809 | rows=32.38 |
| HNSW forced (vector) | 40 | relaxed_order | 4.418 | 4.824 | 4.981 | 1.0 | 0.9988 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 4.4 | 4.581 | 4.959 | 1.0 | 0.9933 | rows=39.73 |
| HNSW forced (vector) | 100 | relaxed_order | 4.432 | 5.135 | 5.25 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 4.603 | 5.176 | 5.211 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (vector) | 200 | relaxed_order | 4.769 | 5.558 | 6.03 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 4.225 | 4.662 | 4.945 | 1.0 | 0.9995 | rows=40.0 |

**ws1 (200 chunks, 100 queries; eligible-id array build p50 1.841 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 4.866 | 4.982 | 5.134 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 4.484 | 4.766 | 5.639 | 1.0 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 4.578 | 5.512 | 5.918 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 3.719 | 3.937 | 4.066 | 0.88 | 0.4525 | rows=18.1 |
| HNSW forced (vector) | 40 | relaxed_order | 4.728 | 14.176 | 18.269 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 4.733 | 10.878 | 25.139 | 0.99 | 0.77 | rows=30.8 |
| HNSW forced (vector) | 100 | relaxed_order | 4.565 | 5.662 | 6.112 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 4.603 | 5.269 | 5.438 | 1.0 | 0.9425 | rows=37.7 |
| HNSW forced (vector) | 200 | relaxed_order | 4.849 | 6.397 | 6.514 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 4.2 | 4.968 | 5.138 | 1.0 | 1.0 | rows=40.0 |

**ws2 (70 chunks, 100 queries; eligible-id array build p50 1.705 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 3.653 | 3.736 | 3.876 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 3.545 | 3.655 | 3.852 | 1.0 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 3.861 | 3.999 | 4.029 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 4.165 | 4.717 | 4.88 | 0.775 | 0.2938 | rows=11.75 |
| HNSW forced (vector) | 40 | relaxed_order | 6.403 | 7.079 | 7.192 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 4.203 | 4.943 | 5.688 | 0.875 | 0.4125 | rows=16.5 |
| HNSW forced (vector) | 100 | relaxed_order | 6.358 | 7.092 | 7.568 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 4.38 | 5.07 | 5.208 | 1.0 | 0.6937 | rows=27.75 |
| HNSW forced (vector) | 200 | relaxed_order | 6.62 | 8.061 | 8.532 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 5.335 | 6.496 | 7.109 | 1.0 | 1.0 | rows=40.0 |

**ws3 (20 chunks, 100 queries; eligible-id array build p50 1.636 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 2.974 | 3.057 | 3.152 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 2.936 | 3.117 | 3.302 | 1.0 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 3.381 | 3.535 | 3.63 | 1.0 | 1.0 | hnsw=False, rows=20.0 |
| HNSW forced (vector) | 40 | off | 3.924 | 4.541 | 4.781 | 1.0 | 1.0 | rows=20.0 |
| HNSW forced (vector) | 40 | relaxed_order | 6.862 | 7.592 | 8.356 | 1.0 | 1.0 | rows=20.0 |
| HNSW forced (vector) | 100 | off | 3.739 | 4.192 | 4.419 | 1.0 | 1.0 | rows=20.0 |
| HNSW forced (vector) | 100 | relaxed_order | 6.301 | 6.571 | 6.703 | 1.0 | 1.0 | rows=20.0 |
| HNSW forced (vector) | 200 | off | 3.733 | 4.015 | 4.111 | 1.0 | 1.0 | rows=20.0 |
| HNSW forced (vector) | 200 | relaxed_order | 6.504 | 6.918 | 9.071 | 1.0 | 1.0 | rows=20.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 6.176 | 7.437 | 9.696 | 1.0 | 1.0 | rows=20.0 |

**ws4 (10 chunks, 100 queries; eligible-id array build p50 1.391 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 2.774 | 2.899 | 3.031 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 2.718 | 2.785 | 3.178 | 1.0 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 3.032 | 3.229 | 3.429 | 1.0 | 1.0 | hnsw=False, rows=10.0 |
| HNSW forced (vector) | 40 | off | 3.669 | 4.219 | 4.378 | 1.0 | 1.0 | rows=10.0 |
| HNSW forced (vector) | 40 | relaxed_order | 6.491 | 7.074 | 7.238 | 1.0 | 1.0 | rows=10.0 |
| HNSW forced (vector) | 100 | off | 3.611 | 3.833 | 3.924 | 1.0 | 1.0 | rows=10.0 |
| HNSW forced (vector) | 100 | relaxed_order | 6.954 | 9.005 | 10.025 | 1.0 | 1.0 | rows=10.0 |
| HNSW forced (vector) | 200 | off | 4.553 | 5.522 | 5.816 | 1.0 | 1.0 | rows=10.0 |
| HNSW forced (vector) | 200 | relaxed_order | 6.712 | 7.151 | 8.733 | 1.0 | 1.0 | rows=10.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 6.372 | 13.725 | 19.798 | 1.0 | 1.0 | rows=10.0 |

| workspace | chunks | p50 ms | p95 ms | p99 ms | mean hits |
|---|---|---|---|---|---|
| ws0 | 700 | 2.891 | 4.19 | 5.652 | 3.48 |
| ws4 | 10 | 1.836 | 2.989 | 4.845 | 0.07 |

Concurrency, requested mode `auto`:

| clients | queries | qps | p50 ms | p95 ms | p99 ms | mode used |
|---|---|---|---|---|---|---|
| 1 | 200 | 63.7 | 14.209 | 20.143 | 40.045 | exact |
| 10 | 200 | 146.09 | 65.33 | 94.89 | 108.028 | exact |
| 50 | 250 | 128.82 | 368.344 | 532.326 | 567.443 | exact |

### Scale 10000 chunks
Load+index 36.1 s; build s: {'halfvec_table_s': 0.521, 'hnsw_halfvec_s': 4.928, 'hnsw_vector_s': 10.645}; free disk GB before/after load/after measure: 12.29/12.16/12.05

| metric | vector(1536) | halfvec(1536) |
|---|---|---|
| avg embedding column bytes | 6148.0 | 3076.0 |
| table heap bytes / embedding | 8395.2 | 4247.6 |
| HNSW bytes / embedding | 8192.8 | 4096.8 |
| HNSW index total bytes | 81928192 | 40968192 |

**ws0 (7000 chunks, 100 queries; eligible-id array build p50 11.79 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 79.064 | 137.915 | 215.693 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 62.149 | 86.709 | 95.483 | 0.999 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 45.542 | 50.982 | 55.455 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 25.059 | 34.104 | 39.355 | 0.998 | 0.771 | rows=30.86 |
| HNSW forced (vector) | 40 | relaxed_order | 23.684 | 27.573 | 29.994 | 1.0 | 0.9992 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 23.287 | 25.407 | 26.125 | 1.0 | 0.9992 | rows=39.97 |
| HNSW forced (vector) | 100 | relaxed_order | 23.111 | 44.86 | 72.478 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 34.683 | 53.507 | 68.892 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (vector) | 200 | relaxed_order | 23.503 | 25.769 | 27.266 | 1.0 | 1.0 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 23.933 | 32.712 | 38.433 | 0.999 | 0.9997 | rows=40.0 |

**ws1 (2000 chunks, 100 queries; eligible-id array build p50 4.955 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 26.228 | 33.86 | 40.197 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 21.534 | 24.444 | 29.186 | 1.0 | 0.9995 | - |
| ann as planned (vector) | 100 | relaxed_order | 16.717 | 19.775 | 26.986 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 7.104 | 12.279 | 14.603 | 0.891 | 0.425 | rows=17.0 |
| HNSW forced (vector) | 40 | relaxed_order | 6.895 | 12.652 | 16.306 | 0.999 | 0.9567 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 5.663 | 6.545 | 9.127 | 0.992 | 0.7278 | rows=29.13 |
| HNSW forced (vector) | 100 | relaxed_order | 6.962 | 9.94 | 11.609 | 0.999 | 0.962 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 6.342 | 7.567 | 7.898 | 0.999 | 0.894 | rows=36.1 |
| HNSW forced (vector) | 200 | relaxed_order | 6.763 | 10.174 | 11.318 | 0.999 | 0.9645 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 6.654 | 10.412 | 12.45 | 0.999 | 0.9635 | rows=40.0 |

**ws2 (700 chunks, 100 queries; eligible-id array build p50 2.629 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 11.948 | 14.733 | 30.143 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 10.105 | 10.974 | 12.904 | 1.0 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 9.433 | 11.947 | 18.337 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 4.824 | 6.881 | 7.03 | 0.758 | 0.3463 | rows=13.85 |
| HNSW forced (vector) | 40 | relaxed_order | 10.086 | 15.168 | 18.078 | 0.997 | 0.8892 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 5.102 | 5.944 | 6.156 | 0.901 | 0.4892 | rows=19.57 |
| HNSW forced (vector) | 100 | relaxed_order | 9.829 | 13.828 | 15.465 | 0.997 | 0.8908 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 5.904 | 6.982 | 7.245 | 0.994 | 0.6532 | rows=26.19 |
| HNSW forced (vector) | 200 | relaxed_order | 10.888 | 16.051 | 20.443 | 0.997 | 0.9095 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 8.746 | 12.052 | 13.981 | 0.997 | 0.8885 | rows=40.0 |

**ws3 (200 chunks, 100 queries; eligible-id array build p50 1.984 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 5.596 | 6.553 | 6.733 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 5.867 | 6.453 | 7.457 | 1.0 | 0.9968 | - |
| ann as planned (vector) | 100 | relaxed_order | 5.528 | 6.436 | 8.977 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 5.061 | 6.084 | 6.586 | 0.763 | 0.412 | rows=16.48 |
| HNSW forced (vector) | 40 | relaxed_order | 22.581 | 32.166 | 34.011 | 0.988 | 0.9213 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 5.467 | 6.644 | 7.737 | 0.788 | 0.4717 | rows=18.87 |
| HNSW forced (vector) | 100 | relaxed_order | 21.178 | 26.53 | 28.887 | 0.988 | 0.9243 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 5.761 | 6.915 | 7.172 | 0.952 | 0.5355 | rows=21.42 |
| HNSW forced (vector) | 200 | relaxed_order | 28.437 | 66.181 | 137.752 | 0.988 | 0.9275 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 19.477 | 31.928 | 71.635 | 1.0 | 0.9337 | rows=40.0 |

**ws4 (100 chunks, 100 queries; eligible-id array build p50 2.221 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 4.705 | 5.199 | 5.414 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 4.545 | 5.32 | 7.507 | 1.0 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 4.662 | 6.605 | 7.842 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 4.784 | 5.816 | 5.932 | 0.7 | 0.345 | rows=13.8 |
| HNSW forced (vector) | 40 | relaxed_order | 25.535 | 33.847 | 36.017 | 1.0 | 0.965 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 5.575 | 10.126 | 11.366 | 0.8 | 0.375 | rows=15.0 |
| HNSW forced (vector) | 100 | relaxed_order | 25.619 | 36.092 | 58.049 | 1.0 | 0.965 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 6.932 | 13.073 | 18.592 | 0.84 | 0.425 | rows=17.0 |
| HNSW forced (vector) | 200 | relaxed_order | 26.611 | 35.03 | 41.736 | 1.0 | 0.965 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 21.292 | 32.157 | 38.359 | 1.0 | 0.965 | rows=40.0 |

| workspace | chunks | p50 ms | p95 ms | p99 ms | mean hits |
|---|---|---|---|---|---|
| ws0 | 7000 | 2.948 | 12.904 | 16.05 | 12.99 |
| ws4 | 100 | 2.318 | 3.228 | 3.653 | 0.82 |

Concurrency, requested mode `auto`:

| clients | queries | qps | p50 ms | p95 ms | p99 ms | mode used |
|---|---|---|---|---|---|---|
| 1 | 200 | 10.4 | 89.419 | 128.171 | 184.38 | exact |
| 10 | 200 | 12.38 | 637.665 | 1450.424 | 1746.445 | exact |
| 50 | 250 | 13.59 | 3598.638 | 4454.603 | 4827.524 | exact |

### Scale 30000 chunks
Load+index 146.0 s; build s: {'halfvec_table_s': 1.562, 'hnsw_halfvec_s': 18.912, 'hnsw_vector_s': 40.058}; free disk GB before/after load/after measure: 12.05/11.41/11.46

| metric | vector(1536) | halfvec(1536) |
|---|---|---|
| avg embedding column bytes | 6148.0 | 3076.0 |
| table heap bytes / embedding | 8391.3 | 4228.7 |
| HNSW bytes / embedding | 8192.3 | 4096.3 |
| HNSW index total bytes | 245768192 | 122888192 |

**ws0 (21000 chunks, 100 queries; eligible-id array build p50 30.994 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 300.255 | 369.889 | 429.664 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 191.112 | 323.482 | 465.13 | 1.0 | 0.9997 | - |
| ann as planned (vector) | 100 | relaxed_order | 189.095 | 218.37 | 236.668 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 113.109 | 212.277 | 407.058 | 0.969 | 0.7635 | rows=31.45 |
| HNSW forced (vector) | 40 | relaxed_order | 100.007 | 120.175 | 126.554 | 0.984 | 0.9765 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 99.017 | 111.968 | 119.822 | 0.99 | 0.9785 | rows=39.57 |
| HNSW forced (vector) | 100 | relaxed_order | 100.415 | 113.393 | 178.501 | 0.99 | 0.9892 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 104.235 | 119.46 | 274.253 | 1.0 | 0.9988 | rows=39.98 |
| HNSW forced (vector) | 200 | relaxed_order | 101.285 | 110.285 | 116.775 | 1.0 | 0.9992 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 90.016 | 202.613 | 264.167 | 1.0 | 0.9992 | rows=40.0 |

**ws1 (6000 chunks, 100 queries; eligible-id array build p50 10.596 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 68.689 | 82.076 | 87.763 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 53.94 | 60.808 | 68.116 | 1.0 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 40.675 | 46.23 | 53.679 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 48.618 | 71.641 | 83.473 | 0.832 | 0.4178 | rows=17.29 |
| HNSW forced (vector) | 40 | relaxed_order | 48.562 | 60.267 | 71.688 | 0.988 | 0.9283 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 46.462 | 66.904 | 129.579 | 0.983 | 0.7163 | rows=28.74 |
| HNSW forced (vector) | 100 | relaxed_order | 44.048 | 55.404 | 61.713 | 0.993 | 0.944 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 42.506 | 47.861 | 96.481 | 0.993 | 0.884 | rows=35.71 |
| HNSW forced (vector) | 200 | relaxed_order | 44.746 | 55.829 | 62.215 | 0.993 | 0.947 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 11.06 | 34.797 | 73.527 | 0.995 | 0.946 | rows=40.0 |

**ws2 (2100 chunks, 100 queries; eligible-id array build p50 4.811 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 27.114 | 31.64 | 44.952 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 21.781 | 25.78 | 31.563 | 0.998 | 0.9992 | - |
| ann as planned (vector) | 100 | relaxed_order | 17.374 | 21.062 | 25.957 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 37.558 | 42.642 | 46.112 | 0.693 | 0.3005 | rows=12.34 |
| HNSW forced (vector) | 40 | relaxed_order | 46.327 | 62.087 | 70.003 | 0.987 | 0.8515 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 36.743 | 39.202 | 40.217 | 0.879 | 0.4688 | rows=19.06 |
| HNSW forced (vector) | 100 | relaxed_order | 44.655 | 56.076 | 64.493 | 0.986 | 0.8555 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 38.555 | 44.101 | 48.146 | 0.968 | 0.6622 | rows=26.62 |
| HNSW forced (vector) | 200 | relaxed_order | 47.253 | 62.408 | 98.171 | 0.992 | 0.8668 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 9.98 | 18.959 | 21.696 | 0.996 | 0.871 | rows=40.0 |

**ws3 (600 chunks, 100 queries; eligible-id array build p50 2.92 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 11.213 | 12.706 | 14.287 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 9.475 | 11.272 | 12.577 | 1.0 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 9.189 | 10.476 | 11.772 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 34.717 | 36.481 | 37.388 | 0.569 | 0.2457 | rows=9.83 |
| HNSW forced (vector) | 40 | relaxed_order | 63.135 | 93.531 | 99.812 | 1.0 | 0.8772 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 35.569 | 39.468 | 44.689 | 0.866 | 0.3987 | rows=15.95 |
| HNSW forced (vector) | 100 | relaxed_order | 63.108 | 92.994 | 102.255 | 1.0 | 0.8808 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 35.866 | 37.338 | 38.225 | 1.0 | 0.5345 | rows=21.38 |
| HNSW forced (vector) | 200 | relaxed_order | 74.471 | 178.227 | 216.922 | 1.0 | 0.884 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 17.68 | 31.599 | 38.604 | 0.997 | 0.8795 | rows=40.0 |

**ws4 (300 chunks, 100 queries; eligible-id array build p50 2.365 ms)**

| path | ef_search | iterative_scan | p50 ms | p95 ms | p99 ms | recall@10 | recall@40 | notes |
|---|---|---|---|---|---|---|---|---|
| exact vector (seq scan) | - | - | 7.133 | 10.907 | 15.804 | 1.0000 | 1.0000 | - |
| exact halfvec | - | - | 6.396 | 7.395 | 8.535 | 1.0 | 1.0 | - |
| ann as planned (vector) | 100 | relaxed_order | 6.452 | 6.959 | 7.255 | 1.0 | 1.0 | hnsw=False, rows=40.0 |
| HNSW forced (vector) | 40 | off | 35.159 | 45.52 | 67.312 | 0.67 | 0.3498 | rows=13.99 |
| HNSW forced (vector) | 40 | relaxed_order | 73.391 | 130.691 | 151.988 | 1.0 | 0.9065 | rows=40.0 |
| HNSW forced (vector) | 100 | off | 34.698 | 53.354 | 62.634 | 0.84 | 0.4135 | rows=16.54 |
| HNSW forced (vector) | 100 | relaxed_order | 77.671 | 134.191 | 157.057 | 1.0 | 0.9065 | rows=40.0 |
| HNSW forced (vector) | 200 | off | 35.465 | 38.6 | 60.623 | 0.965 | 0.5295 | rows=21.18 |
| HNSW forced (vector) | 200 | relaxed_order | 74.405 | 130.539 | 132.696 | 1.0 | 0.9065 | rows=40.0 |
| HNSW forced (halfvec) | 100 | relaxed_order | 22.211 | 42.684 | 46.2 | 1.0 | 0.8912 | rows=40.0 |

| workspace | chunks | p50 ms | p95 ms | p99 ms | mean hits |
|---|---|---|---|---|---|
| ws0 | 21000 | 3.602 | 32.565 | 40.36 | 14.5 |
| ws4 | 300 | 2.478 | 2.872 | 2.984 | 1.94 |

Concurrency, requested mode `ann`:

| clients | queries | qps | p50 ms | p95 ms | p99 ms | mode used |
|---|---|---|---|---|---|---|
| 1 | 200 | 3.6 | 197.165 | 756.781 | 1440.075 | ann |
| 10 | 200 | 10.04 | 860.78 | 1490.685 | 2807.672 | ann |
| 50 | 250 | 10.66 | 4583.367 | 5366.539 | 5616.747 | ann |

Concurrency, requested mode `auto`:

| clients | queries | qps | p50 ms | p95 ms | p99 ms | mode used |
|---|---|---|---|---|---|---|
| 1 | 200 | 4.48 | 201.123 | 342.843 | 552.535 | ann |
| 10 | 200 | 11.7 | 836.406 | 1049.578 | 1102.446 | ann |
| 50 | 250 | 11.04 | 4424.353 | 5276.475 | 5418.634 | ann |

Concurrency, requested mode `exact`:

| clients | queries | qps | p50 ms | p95 ms | p99 ms | mode used |
|---|---|---|---|---|---|---|
| 1 | 200 | 3.11 | 302.491 | 362.568 | 621.357 | exact |
| 10 | 200 | 5.51 | 1790.959 | 1978.239 | 2041.58 | exact |
| 50 | 250 | 5.12 | 9214.054 | 11958.039 | 12299.718 | exact |

### Crossover (ef_search=100, relaxed_order; workspace chunks = rows eligible for the query)
| workspace chunks | scale | ws | exact p50 | exact p95 | ann-as-planned p50 | ann uses HNSW | ann recall@10 | HNSW-forced p50 | HNSW-forced p95 | HNSW-forced recall@10 | eligible-array p50 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 1000 | ws4 | 2.774 | 2.899 | 3.032 | False | 1.0 | 6.954 | 9.005 | 1.0 | 1.391 |
| 20 | 1000 | ws3 | 2.974 | 3.057 | 3.381 | False | 1.0 | 6.301 | 6.571 | 1.0 | 1.636 |
| 70 | 1000 | ws2 | 3.653 | 3.736 | 3.861 | False | 1.0 | 6.358 | 7.092 | 1.0 | 1.705 |
| 100 | 10000 | ws4 | 4.705 | 5.199 | 4.662 | False | 1.0 | 25.619 | 36.092 | 1.0 | 2.221 |
| 200 | 1000 | ws1 | 4.866 | 4.982 | 4.578 | False | 1.0 | 4.565 | 5.662 | 1.0 | 1.841 |
| 200 | 10000 | ws3 | 5.596 | 6.553 | 5.528 | False | 1.0 | 21.178 | 26.53 | 0.988 | 1.984 |
| 300 | 30000 | ws4 | 7.133 | 10.907 | 6.452 | False | 1.0 | 77.671 | 134.191 | 1.0 | 2.365 |
| 600 | 30000 | ws3 | 11.213 | 12.706 | 9.189 | False | 1.0 | 63.108 | 92.994 | 1.0 | 2.92 |
| 700 | 1000 | ws0 | 9.921 | 10.588 | 6.559 | False | 1.0 | 4.432 | 5.135 | 1.0 | 2.446 |
| 700 | 10000 | ws2 | 11.948 | 14.733 | 9.433 | False | 1.0 | 9.829 | 13.828 | 0.997 | 2.629 |
| 2000 | 10000 | ws1 | 26.228 | 33.86 | 16.717 | False | 1.0 | 6.962 | 9.94 | 0.999 | 4.955 |
| 2100 | 30000 | ws2 | 27.114 | 31.64 | 17.374 | False | 1.0 | 44.655 | 56.076 | 0.986 | 4.811 |
| 6000 | 30000 | ws1 | 68.689 | 82.076 | 40.675 | False | 1.0 | 44.048 | 55.404 | 0.993 | 10.596 |
| 7000 | 10000 | ws0 | 79.064 | 137.915 | 45.542 | False | 1.0 | 23.111 | 44.86 | 1.0 | 11.79 |
| 21000 | 30000 | ws0 | 300.255 | 369.889 | 189.095 | False | 1.0 | 100.415 | 113.393 | 0.99 | 30.994 |

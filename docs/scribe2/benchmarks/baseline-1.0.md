# Scribe 1.0 frozen retrieval baseline (T07), live run

**PRELIMINARY — golden set unreviewed, not publishable (INV-12)**

Every number in this file is a measurement of the unmodified Scribe 1.0 TypeScript pipeline on the unreviewed golden set. They are labels for plumbing and for paired comparison, not quality claims. Only dev and validation splits were scored; the test split was not scored (INV-12).

## 1. Header (INV-11)

| Field | Value |
|---|---|
| Run date | 2026-10-12 (UTC 10:43) |
| Dataset | `golden-v1`, 200 cases (120 original plus 80 added in T28, both NOT HUMAN-REVIEWED; see `benchmarks/datasets/golden-v1/REVIEW_FIRST.md`). Scored: dev 80 cases (72 answerable, 8 unanswerable), validation 40 cases (36 answerable, 4 unanswerable) |
| Code commit | `722275a5e61e2b5ee8607d00698fa2547c4bb1e7` (no dirty files in the TS pipeline) |
| Embedding model | `models/gemini-embedding-2-preview`, 3072 dimensions, plain text (no task prefix), real Gemini API |
| Chunker | `RecursiveCharacterTextSplitter` 4000 / 400, separators `\n\n`, `\n`, ` `, `` |
| Retrieval | `lib/search.ts` `searchDocuments`, top-k 10, dense cosine only |
| Variants | `threshold-0.3` (as chat calls it) and `threshold-none` (-1) |
| Driver | `psql` against local Postgres (pgvector) container `scribe-db-bridge`, throwaway DB `baseline_live_test`; the `neon` driver was not run (needs a Neon HTTP endpoint) |
| Node | v18.16.0 (`pdf_parser_state_failures: []`; offline check gave 24 docs, 21 extracted, 23 chunks, 3 failures, as expected) |
| Meta | `docs/scribe2/benchmarks/baseline-1.0-meta.json` (copy of `baseline-meta.json`) |

The howto did not require the test split for the baseline, so nothing was skipped. The test split's ranking lines exist in the (git-ignored) rankings files but were not scored.

## 2. Overall results (scribe-eval score)

Unanswerable cases are excluded from the means. The two threshold variants gave **identical** metrics: every query returned 10 chunks and the lowest cosine score in any list was 0.521 (the index is 23 chunks), so the threshold removes nothing. For unanswerable cases the max top score was 0.778 (dev) and 0.741 (validation), i.e. the 0.3 threshold can never make 1.0 abstain.

### dev (threshold-0.3 and threshold-none)

```
category                 n   recall@1   recall@5  recall@10        mrr    ndcg@10 precision@5
cross_document           8      0.375      0.750      0.750      0.812      0.730      0.300
disambiguation           8      0.750      0.750      0.750      0.750      0.750      0.225
exception_qualified      8      0.875      1.000      1.000      0.938      0.755      0.200
identifier_exact         8      0.750      0.750      0.750      0.750      0.720      0.150
numeric_reasoning        8      0.688      0.750      0.750      0.750      0.643      0.175
paraphrase_semantic      8      0.500      1.000      1.000      0.729      0.799      0.200
single_fact              8      0.875      1.000      1.000      0.938      0.954      0.200
table_lookup             8      0.500      0.500      0.500      0.500      0.500      0.100
version_conflict         8      0.688      1.000      1.000      0.938      0.959      0.300
---------------------------------------------------------------------------------------------
OVERALL                 72      0.667      0.833      0.833      0.789      0.757      0.206
95% CI (overall): recall@1 [0.562, 0.764], recall@5 [0.743, 0.910], recall@10 [0.743, 0.910], mrr [0.699, 0.873], ndcg@10 [0.669, 0.835], precision@5 [0.178, 0.233]
```

### validation (threshold-0.3 and threshold-none)

```
category                 n   recall@1   recall@5  recall@10        mrr    ndcg@10 precision@5
cross_document           4      0.375      0.875      0.875      0.875      0.847      0.350
disambiguation           4      0.750      1.000      1.000      0.875      0.908      0.250
exception_qualified      4      1.000      1.000      1.000      1.000      0.820      0.200
identifier_exact         4      0.750      1.000      1.000      0.875      0.908      0.200
numeric_reasoning        4      0.875      1.000      1.000      1.000      0.884      0.300
paraphrase_semantic      4      0.750      1.000      1.000      0.800      0.847      0.200
single_fact              4      1.000      1.000      1.000      1.000      1.000      0.200
table_lookup             4      0.250      0.250      0.250      0.250      0.250      0.050
version_conflict         4      0.750      1.000      1.000      1.000      0.903      0.350
---------------------------------------------------------------------------------------------
OVERALL                 36      0.722      0.903      0.903      0.853      0.819      0.233
95% CI (overall): recall@1 [0.583, 0.847], recall@5 [0.806, 0.986], recall@10 [0.806, 0.986], mrr [0.744, 0.944], ndcg@10 [0.714, 0.910], precision@5 [0.200, 0.272]
```

Run ids (git-ignored `benchmarks/runs/`): dev 7b8c5d23 / 216bb033, validation fa51a220 / 07a41788 (threshold-0.3 / threshold-none).

## 3. Per-category results

Included in the tables above (nine answerable categories; `unanswerable` has no relevance metrics). Weakest: `table_lookup` (dev recall@5 0.500, validation 0.250), `cross_document` recall@1 (0.375 on both; recall@5 0.750 and 0.875). Sample sizes are 8 (dev) and 4 (validation) per category, so category numbers are very noisy.

## 4. Per-format results (grouped by case `tags`, answerable cases, same for both variants)

A case with several tags counts in each group. Recall@1 / @5 / @10, MRR, nDCG@10.

| tag | dev n | dev r@1 | dev r@5 | dev r@10 | dev MRR | dev nDCG | val n | val r@1 | val r@5 | val r@10 | val MRR | val nDCG |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pdf | 38 | 0.684 | 0.934 | 0.934 | 0.864 | 0.864 | 15 | 0.667 | 0.967 | 0.967 | 0.933 | 0.904 |
| docx | 20 | 0.600 | 0.725 | 0.725 | 0.725 | 0.644 | 12 | 0.583 | 0.792 | 0.792 | 0.725 | 0.671 |
| md | 9 | 0.833 | 0.944 | 0.944 | 0.944 | 0.905 | 6 | 0.833 | 1.000 | 1.000 | 0.917 | 0.938 |
| txt | 5 | 0.700 | 1.000 | 1.000 | 0.900 | 0.785 | 4 | 0.750 | 1.000 | 1.000 | 1.000 | 0.981 |
| csv | 6 | 0.250 | 0.250 | 0.250 | 0.333 | 0.269 | 5 | 0.700 | 0.800 | 0.800 | 0.800 | 0.723 |
| scanned | 2 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0 | n/a | n/a | n/a | n/a | n/a |
| table | 8 | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 | 4 | 0.250 | 0.250 | 0.250 | 0.250 | 0.250 |
| multi-doc | 12 | 0.417 | 0.917 | 0.917 | 0.917 | 0.876 | 8 | 0.500 | 0.938 | 0.938 | 0.938 | 0.865 |
| identifier | 5 | 1.000 | 1.000 | 1.000 | 1.000 | 0.952 | 2 | 0.500 | 1.000 | 1.000 | 0.750 | 0.815 |
| numeric | 5 | 0.800 | 0.800 | 0.800 | 0.800 | 0.629 | 2 | 0.750 | 1.000 | 1.000 | 1.000 | 0.769 |
| version | 5 | 0.600 | 1.000 | 1.000 | 0.900 | 0.934 | 2 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 |

## 5. Ingestion

24 documents: 21 extracted, 3 failed, 23 chunks in total (`fleet-inventory.csv` and `onboarding-handbook.pdf` have 2 chunks each, the other 19 have 1). Failures (real Scribe 1.0 weaknesses, recorded as `files.status='failed'`, no chunks):

| Document | Error |
|---|---|
| `facility-safety-notice-scanned.pdf` | No text extracted ... Scanned PDFs and image-only documents are not supported yet. |
| `office-locations.csv` | Invalid Record Length: expect 7, got 6 on line 2 |
| `price-list-2026.csv` | Invalid Record Length: expect 1, got 2 on line 2 |

Per-document stats are in the meta file. Document embedding: 21 batch requests (one per extracted file).

## 6. Latency and cost

Over the 120 dev plus validation cases, per query:

| Variant | median latency_ms | p95 latency_ms | What it includes |
|---|---|---|---|
| threshold-0.3 | 1205.95 | 1290.6 | query embedding API round trip plus psql spawn and query |
| threshold-none | 52.0 | 56.8 | psql only (the query embedding is a cache hit) |

Embedding requests: 21 for documents, 200 for queries (one per case, all 200 golden cases including test-split queries, because the script retrieves for every case), about 221 in all. Indicative only (see artifacts).

## 7. Known measurement artifacts

- CSV rows are flattened by joining cells with spaces, so evidence quotes copied as raw CSV lines do not match under D17 for `benefits-table.csv` and `fleet-inventory.csv`; table cases can score 0 though the right chunk is retrieved. Visible in the `table` / `table_lookup` / `csv` rows.
- The scanned PDF and two CSVs fail extraction (section 5). Dev `scanned` cases (2) score 0 by construction; cases that need `office-locations.csv` or `price-list-2026.csv` are unanswerable for 1.0.
- Near-whole-document chunks: 23 chunks in the index, top-10 covers almost half of it, so recall@10 equals recall@5 here and precision is low by construction. Not comparable with a structural chunker's many small chunks.
- Relevance is substring based (D17); a whole-document chunk contains every quote of its document, including those of the wrong version. Version cases are judged through `forbidden`.
- Queries are raw case questions; production chat may rewrite them.
- Latency is not production latency (psql spawn, embedding round trip; the second variant is a cache hit).
- Preview embedding model can change without notice; the cache `benchmarks/cache/ts-embeddings.jsonl` (git-ignored, 8.7 MB, created by this run) pins what was measured.
- The golden set is not human-reviewed; label errors appear as baseline errors. The 200-case set also includes the 80 T28 cases.
- The eval workspace is the only data indexed, so there are no distractors.
- The `threshold-0.3` and `threshold-none` results are identical, so the baseline has effectively one variant.

## 8. Reproduction

```bash
docker exec scribe-db-bridge psql -U scribe -d postgres -c 'create database baseline_live_test'
bash scripts/apply-migrations.sh postgresql://scribe:scribe@localhost:54329/baseline_live_test
export PATH=/usr/local/bin:$PATH            # Node 18.16
export EVAL_DATABASE_URL=postgresql://scribe:scribe@localhost:54329/baseline_live_test EVAL_ALLOW_WRITE=1
export GOOGLE_GENERATIVE_AI_API_KEY=...     # shell only
npx tsx scripts/eval-baseline.ts --driver psql --reset
for v in threshold-0.3 threshold-none; do for s in dev validation; do
  uv run --project intelligence scribe-eval score --rankings benchmarks/runs/baseline-1.0/rankings.$v.jsonl \
    --dataset benchmarks/datasets/golden-v1 --split $s --profile baseline-1.0-$v \
    --model embedding=models/gemini-embedding-2-preview --out benchmarks/runs
done; done
```

Workspace: kind `eval`, owner `eval-golden-v1`, in the throwaway DB. The `psql` driver was used alone; the neon cross-check was not possible locally.

# M1 preliminary retrieval results (profiles A, B, C vs Scribe 1.0)

**PRELIMINARY — golden set unreviewed, not publishable (INV-12)**

These are measurements, but of an unreviewed golden set, with the rerank profiles missing. They are for plumbing checks and paired comparison only. Do not quote them as quality figures. The test split was not scored, no `--milestone-report` was used, and nothing was tuned.

## 1. Header (INV-11)

| Field | Value |
|---|---|
| Date | 2026-10-12 |
| Code commit | `722275a` plus uncommitted `run-profile` command (committed together with this file) |
| Dataset | `golden-v1`, 200 cases, NOT HUMAN-REVIEWED. Scored: dev 80 (72 answerable, 8 unanswerable), validation 40 (36 answerable, 4 unanswerable). Test: not run |
| Embedding model (2.0) | `gemini-embedding-2`, 1536 dims, real Gemini API (documents embedded in this run: 187 chunks, 28 requests; query vectors came from the on-disk embedding cache, 0 new requests) |
| Embedding model (1.0 baseline) | `models/gemini-embedding-2-preview`, 3072 dims (`baseline-1.0.md`) |
| Rerank model | none (Voyage `rerank-2.5` not available: no key) |
| Database | local Postgres container `scribe-db-bridge`, throwaway DB `eval_live_test`, pgvector 0.8.7, eval workspace `eval-golden-v1`, 24 documents, 214 chunks (187 embedded), all documents ACTIVE |
| Profiles run | A `dense-only`, B `lexical-only`, C `hybrid-rrf` (dense 40 + lexical 40, RRF k=60). Not run: D `dense-rerank`, E `hybrid-rrf-rerank` (need Voyage) |
| Ranking depth | top 20 per case; metrics at k = 1, 5, 10 |
| Commands | `scribe-eval ingest-corpus --embedder gemini --reset`, `scribe-eval run-profile`, `scribe-eval score`, `scribe-eval compare` |

## 2. Read this before the numbers: what the deltas confound

The comparison with Scribe 1.0 is a system-level comparison, not a retrieval-algorithm comparison. Between 1.0 and these runs three things changed at once:

1. **Chunking.** 1.0 had 23 near-whole-document chunks; 2.0 has 187 structural chunks (headings, tables, CSV rows with parents). Recall@10 of 1.0 is capped because the top 10 covered about half of its index.
2. **Ingestion.** 1.0 failed 3 documents (scanned PDF, two CSVs). 2.0 indexed all 24 (the scanned PDF through Gemini OCR). Cases that need those documents were unanswerable for 1.0.
3. **Embedding model/dimension** (preview 3072 vs `gemini-embedding-2` 1536).

So the positive deltas below mostly say "the new ingestion and chunking pipeline finds the right chunk more often", and cannot be attributed to hybrid fusion. The dense vs hybrid comparison (section 5) is the like-for-like one.

## 3. Results (answerable cases only; 95% bootstrap CIs over cases)

### dev (n = 72)

| Profile | recall@1 | recall@5 | recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|
| Scribe 1.0 baseline | 0.667 [0.562, 0.764] | 0.833 [0.743, 0.910] | 0.833 [0.743, 0.910] | 0.789 [0.699, 0.873] | 0.757 [0.669, 0.835] |
| A dense-only | 0.757 [0.667, 0.840] | 0.993 [0.979, 1.000] | 1.000 [1.000, 1.000] | 0.924 [0.877, 0.965] | 0.911 [0.874, 0.945] |
| B lexical-only | 0.111 [0.049, 0.188] | 0.111 [0.049, 0.188] | 0.111 [0.049, 0.188] | 0.125 [0.056, 0.208] | 0.111 [0.047, 0.187] |
| C hybrid-rrf | 0.757 [0.667, 0.840] | 0.993 [0.979, 1.000] | 1.000 [1.000, 1.000] | 0.924 [0.877, 0.965] | 0.911 [0.874, 0.945] |

### validation (n = 36)

| Profile | recall@1 | recall@5 | recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|
| Scribe 1.0 baseline | 0.722 [0.583, 0.847] | 0.903 [0.806, 0.986] | 0.903 [0.806, 0.986] | 0.853 [0.744, 0.944] | 0.819 [0.714, 0.910] |
| A dense-only | 0.736 [0.611, 0.861] | 0.931 [0.847, 1.000] | 1.000 [1.000, 1.000] | 0.896 [0.809, 0.972] | 0.889 [0.825, 0.947] |
| B lexical-only | 0.097 [0.014, 0.194] | 0.097 [0.014, 0.194] | 0.097 [0.014, 0.194] | 0.111 [0.028, 0.222] | 0.104 [0.021, 0.216] |
| C hybrid-rrf | 0.792 [0.681, 0.903] | 0.958 [0.889, 1.000] | 1.000 [1.000, 1.000] | 0.934 [0.865, 0.986] | 0.919 [0.864, 0.966] |

The dev split has 80 cases of which 72 answerable; validation 40/36. Per-category and per-tag tables are in the run directories (`benchmarks/runs/m1sc/`, git-ignored); with 8 (dev) and 4 (validation) cases per category they are too noisy to read.

## 4. Paired deltas vs Scribe 1.0 (B minus A, same case ids; `*` = interval excludes 0)

| Profile | Split | recall@1 | recall@5 | recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|
| A dense-only | dev | +0.090 [-0.014, +0.201] | +0.160 [+0.076, +0.250] * | +0.167 [+0.090, +0.257] * | +0.134 [+0.051, +0.225] * | +0.154 [+0.078, +0.238] * |
| A dense-only | validation | +0.014 [-0.153, +0.181] | +0.028 [-0.097, +0.153] | +0.097 [+0.014, +0.208] * | +0.043 [-0.087, +0.183] | +0.071 [-0.037, +0.193] |
| B lexical-only | dev | -0.556 [-0.667, -0.438] * | -0.722 [-0.826, -0.611] * | -0.722 [-0.826, -0.611] * | -0.664 [-0.771, -0.551] * | -0.646 [-0.744, -0.539] * |
| B lexical-only | validation | -0.625 [-0.778, -0.458] * | -0.806 [-0.917, -0.681] * | -0.806 [-0.917, -0.681] * | -0.742 [-0.878, -0.589] * | -0.714 [-0.845, -0.572] * |
| C hybrid-rrf | dev | +0.090 [-0.014, +0.201] | +0.160 [+0.076, +0.250] * | +0.167 [+0.090, +0.257] * | +0.134 [+0.051, +0.225] * | +0.154 [+0.078, +0.238] * |
| C hybrid-rrf | validation | +0.069 [-0.097, +0.236] | +0.056 [-0.056, +0.181] | +0.097 [+0.014, +0.208] * | +0.081 [-0.047, +0.217] | +0.100 [-0.003, +0.219] |

## 5. Hybrid vs dense (C minus A, the like-for-like fusion comparison)

| Split | recall@1 | recall@5 | MRR | nDCG@10 |
|---|---|---|---|---|
| dev | +0.000 [0, 0] | +0.000 [0, 0] | +0.000 [0, 0] | +0.000 [0, 0] |
| validation | +0.056 [0.000, +0.139] | +0.028 [0.000, +0.083] | +0.038 [0.000, +0.101] | +0.029 [0.000, +0.078] |

On dev, hybrid-rrf produced rankings identical to dense-only. On validation, hybrid is slightly better on a few cases, but every interval touches 0. **Fusion shows no demonstrable benefit yet**, and the reason is section 6.

## 6. Finding: lexical retrieval returns nothing for natural-language questions

`lexical-only` returned an empty list for 71 of 80 dev and 36 of 40 validation queries. `lexical.py` uses `websearch_to_tsquery`, which ANDs every term, so a question such as "What does the glossary say Harbour is?" matches no chunk (some term is always missing). Lexical therefore contributes almost nothing to the RRF fusion, which is why C equals A. This is a measured behaviour of the profile as shipped, not a bug in `run-profile`. It was **not** changed here (no tuning in a preliminary run). Candidate fixes (OR semantics with ranking, stopword removal, keyword extraction for lexical only) belong in a tuning step on dev, after the golden set is reviewed.

## 7. Latency per stage (local, warm, single process, milliseconds)

Wall time of the retrieval handler per case on a local Docker Postgres. Query embeddings were cache hits (embed stage 0 ms), so **real query-embedding latency (about 1.2 s per call in the 1.0 baseline run) is not included.** Not production latency.

| Profile | Split | median total | p95 total | stage medians (dense / lexical / hydrate / fusion / expand / pack) |
|---|---|---|---|---|
| A dense-only | dev | 19.6 | 40.7 | 9 / - / 2 / 0 / 1 / 1 |
| A dense-only | validation | 36.0 | 108.2 | 18 / - / 3.5 / 0 / 2 / 1.5 |
| B lexical-only | dev | 16.3 | 78.4 | - / 6 / 0 / 0 / 0 / 0 |
| B lexical-only | validation | 14.1 | 26.4 | - / 6.5 / 0 / 0 / 0 / 0 |
| C hybrid-rrf | dev | 54.9 | 81.1 | 24 / 6 / 5 / 1 / 3 / 2 |
| C hybrid-rrf | validation | 19.3 | 43.4 | 8 / 2 / 2 / 0 / 1 / 1 |

Run-to-run noise is large (hybrid dev median 55 ms against validation 19 ms is warm-up and ordering, not a real difference). Rerank latency is unmeasured.

## 8. Worked cases (dev, profile C unless noted)

1. **tb-04, table lookup fixed.** "After probation, what notice period applies to someone with 5 to 10 years of service?" 1.0 returned whole-document chunks of unrelated documents first (recall 0). C ranks the `Notice periods` section of `employee-contract-template.docx` first. Mostly a chunking win.
2. **dm-01, scanned PDF now reachable.** The question targets `facility-safety-notice-scanned.pdf`, which 1.0 failed to extract. C ranks the OCR'd notice first. This is an ingestion win, not retrieval.
3. **id-16, identifier question still wrong at rank 1.** "What is FUEL-SUR on the price list and from when is it valid?" C ranks the board minutes and the FAQ (both mention the fuel surcharge) above the `price-list-2026.csv` row, which is third (recall@1 0.0, MRR 0.33). Lexical would match the identifier, but see section 6: the AND query returns nothing.
4. **sf-09 / sf-12, single-fact cases at rank 2.** Both recall@1 = 0, MRR 0.5: the right chunk is second. 1.0 got sf-09 at rank 1 (whole-document chunk matched the quote). A reranker is the expected remedy; unmeasured.
5. **cd-17 / cd-11, cross-document.** Needs chunks from two documents; C finds both within the top 10 but not at rank 1 (recall@1 0.0, MRR 0.33 and 0.5). recall@10 is 1.0 on every dev and validation answerable case, so the remaining loss is ordering, not coverage.
6. **Unanswerable cases (8 dev, 4 validation) are not scored for abstention.** There is no calibrated threshold; the best dense cosine for an unanswerable dev query is 0.762 and for validation 0.786, within the range of answerable scores, so cosine cannot gate abstention (as DESIGN D13 expects).

## 9. BLOCKED

Blocked on `VOYAGE_API_KEY` (none exists):
- Profiles D `dense-rerank` and E `hybrid-rrf-rerank` (`run-profile` refuses them rather than degrade silently).
- Calibrating the answerability threshold `tau` (needs reranker scores on dev/validation); until then R3 of the CI gate is weak.
- Choosing the default profile by the written rule (needs D and E against C).
- Flipping `RETRIEVAL_ENGINE` to `python` (T17 rule requires the chosen profile).
- Arming the CI regression gate (`benchmarks/gate/baseline.json`, needs rerank cache and the reviewed golden set).
- Rerank latency and cost numbers.

Blocked on the human golden-set review (`benchmarks/datasets/golden-v1/REVIEW_FIRST.md`):
- Publishing any figure from this file or `baseline-1.0.md` (INV-12).
- The single test-split `--milestone-report` run and `docs/scribe2/benchmarks/m1.md`.
- Lexical-query tuning on dev (do after review so labels are trusted).
- Arming the gate on reviewed data.

## 10. Reproduction

```bash
docker exec scribe-db-bridge psql -U scribe -d postgres -c 'create database eval_live_test'   # then apply migrations
set -a; . /tmp/scribe-gemini.env; set +a                  # GOOGLE_GENERATIVE_AI_API_KEY, never commit
U=postgresql://scribe:scribe@localhost:54329/eval_live_test
uv run --project intelligence scribe-eval ingest-corpus --corpus fixtures/corpus --embedder gemini --database-url $U --reset
for p in dense-only lexical-only hybrid-rrf; do for s in dev validation; do
  uv run --project intelligence scribe-eval run-profile --profile $p --split $s --database-url $U --out benchmarks/runs/m1/$p.$s.jsonl
  uv run --project intelligence scribe-eval score --rankings benchmarks/runs/m1/$p.$s.jsonl --split $s --profile $p \
    --model embedding=gemini-embedding-2 --out benchmarks/runs/m1sc
done; done
uv run --project intelligence scribe-eval compare <baseline run dir> <profile run dir>
```

Run from the repo root (dataset and cache paths are relative). Run artifacts under `benchmarks/runs/` are git-ignored.

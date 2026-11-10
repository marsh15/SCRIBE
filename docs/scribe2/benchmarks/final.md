# Scribe 2.0 final benchmark report: TEMPLATE

**STATUS: NOT MEASURED. This file contains no results.** Every cell says "NOT MEASURED". It is a template: a person fills it only from the run records written by the commands in section 2 (copy numbers from `scribe-eval report` output; never retype them from memory), then deletes this banner. A cell that was not produced by a run stays "NOT MEASURED".

The test split may be scored exactly once, for this report (INV-12). Do not run any test-split command until section 1 is fully met.

## 1. Preconditions (all must be true first)

| # | Precondition | State when this template was written |
|---|---|---|
| 1 | All 200 golden cases human-reviewed (`docs/scribe2/REVIEW_CHECKLIST.md`); `scribe-eval validate benchmarks/datasets/golden-v1 --require-reviewed` exits 0 | NOT MET (0 of 200 reviewed) |
| 2 | `/tmp/scribe-keys.env` (mode 600) holds `GOOGLE_GENERATIVE_AI_API_KEY` and `VOYAGE_API_KEY` | NOT MET (file absent) |
| 3 | A throwaway Postgres whose name contains `test`, schema applied (`scripts/apply-migrations.sh`) | local container only |
| 4 | Profiles scored on dev/validation with real providers, a default chosen by a written rule, `tau` calibrated (T17), lexical `or_rank` profiles evaluated against `and` | NOT MET. Only dense/lexical/hybrid-without-rerank were scored (`m1-prelim.md`, unreviewed labels, `and`-mode lexical). Reranked profiles and `or_rank` have no measurement. |
| 5 | The same corpus ingested by the 1.0 pipeline and scored (`baseline-1.0-howto.md`), repeated after the review | preliminary run exists (`baseline-1.0.md`, unreviewed labels); repeat after review |
| 6 | The gate armed (`scripts/arm-gate.sh`) so later changes are guarded | NOT ARMED |
| 7 | Spend: the ledger shows total provider spend under the $10 cap | NOT CHECKED |

## 2. Commands (run by the owner, in this order)

```bash
docker compose up -d db
PGPASSWORD=scribe psql -h localhost -p 54329 -U scribe -d postgres -c 'CREATE DATABASE scribe_final_test'
export DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/scribe_final_test
scripts/apply-migrations.sh "$DATABASE_URL_TEST"
set -a; source /tmp/scribe-keys.env; set +a                         # shell only; never commit

# 1. index the corpus with the real embedder into the eval workspace
uv run --project intelligence scribe-eval ingest-corpus --corpus fixtures/corpus \
  --embedder gemini --database-url "$DATABASE_URL_TEST" --reset

# 2. dev + validation first (tuning allowed here, test is not touched): every profile you intend to report
for split in dev validation; do
  uv run --project intelligence scribe-eval run-profile --split $split --database-url "$DATABASE_URL_TEST" \
    --profiles dense-only,lexical-only,lexical-only-v2,hybrid-rrf,hybrid-rrf-v2,hybrid-rrf-rerank,hybrid-rrf-rerank-v2 \
    --out benchmarks/runs/rankings-$split
done
# score each rankings file, then `scribe-eval compare <run A> <run B>` for the pairs you report

# 3. the ONE test-split run, for the profile chosen by the written rule (see m1-prelim.md section 9), plus the 1.0 baseline
uv run --project intelligence scribe-eval run-profile --split test --milestone-report --milestone-id final \
  --profile <chosen> --database-url "$DATABASE_URL_TEST" --out benchmarks/runs/rankings-test.jsonl
uv run --project intelligence scribe-eval score --rankings benchmarks/runs/rankings-test.jsonl --split test \
  --profile <chosen> --milestone-report --model embedding=gemini-embedding-2 --model rerank=<voyage model id>
uv run --project intelligence scribe-eval answer --rankings benchmarks/runs/rankings-test.jsonl --split test \
  --profile <chosen> --milestone-report --milestone-id final

# 4. render the tables from the persisted run directories (this is the only source for section 3)
uv run --project intelligence scribe-eval report <run dirs...> --dataset benchmarks/datasets/golden-v1 --out docs/scribe2/benchmarks/final-generated.md
```

## 3. Report skeleton (fill from `final-generated.md` only)

Run record ids (INV-11): dataset version NOT MEASURED, profile hash NOT MEASURED, commit NOT MEASURED, model ids NOT MEASURED.

### 3.1 Retrieval, test split, answerable cases

| System | n | Recall@5 | Hit@5 | MRR | nDCG@10 | P@5 |
|---|---|---|---|---|---|---|
| Scribe 1.0 (threshold 0.3) | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED |
| Scribe 1.0 (no threshold) | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED |
| Scribe 2.0 chosen profile | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED |

Paired bootstrap difference (2.0 minus 1.0) with 95% interval: NOT MEASURED. The 1.0 and 2.0 pipelines differ in chunking, ingestion and embedding model at once; say so (see `m1-prelim.md` section 2).

### 3.2 Lexical channel: `and` versus `or_rank`

Dev and validation only. Share of questions for which the lexical list is non-empty, and the hybrid-versus-dense paired difference, for `hybrid-rrf` versus `hybrid-rrf-v2`: NOT MEASURED.

### 3.3 By category (10 categories)

NOT MEASURED.

### 3.4 Abstention (unanswerable cases)

Missed abstentions and the `tau` used: NOT MEASURED (`tau` is the uncalibrated 0.0 in the repository).

### 3.5 Answer quality and verification

Correctness, citation precision/recall, groundedness, judge agreement kappa on 60 human-labelled answers, false-VERIFIED and false-UNSUPPORTED rates: NOT MEASURED.

### 3.6 Latency and cost

p50/p95 retrieval latency and embedding/rerank cost per query against real providers: NOT MEASURED. (`performance.md` holds synthetic database-only timings; they are not these.)

### 3.7 Worked failures (at least 5)

NOT MEASURED. Use `/eval/[runId]` after `scribe-eval publish-run` (see `T30-notes.md`).

### 3.8 Caveats to write after the run

Review state of the golden set when scored, that the corpus is synthetic Northwind data, sample size per category, deviations from the commands above, total provider spend from the ledger.

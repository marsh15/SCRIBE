# Scribe 2.0 final benchmark report: PLACEHOLDER

**STATUS: NOT MEASURED. This file contains no results.** Every cell below says "NOT MEASURED". Do not quote anything from this file until a person has run the commands in section 2, replaced this skeleton with the real output and removed this banner.

It is empty because the preconditions in section 1 are not met today: the golden set is unreviewed, there is no real baseline, `tau` is uncalibrated, no retrieval profile is chosen, and live providers have never been called. The test split may be scored exactly once, for the milestone report (INV-12), so running it early would spend it.

## 1. Preconditions (all must be true first)

| # | Precondition | State today (from the repo) |
|---|---|---|
| 1 | Golden set human-reviewed (`benchmarks/datasets/golden-v1/REVIEW_FIRST.md`); 80 of the 200 cases are machine-drafted and unreviewed (`UNREVIEWED_T28.md`) | NOT MET |
| 2 | Real `GOOGLE_GENERATIVE_AI_API_KEY` and `VOYAGE_API_KEY` in the shell (never committed) | not checked by this task; no live call has ever been made |
| 3 | Throwaway Postgres whose name contains `test` with the schema applied (`scripts/apply-migrations.sh`) | local container only |
| 4 | Profiles A-E scored on dev/validation, a default chosen by a written rule, `tau` calibrated (task T17) | NOT DONE (T17 skipped) |
| 5 | Scribe 1.0 baseline measured live (`docs/scribe2/benchmarks/baseline-1.0-howto.md`, Node 18) and written to `docs/scribe2/benchmarks/baseline-1.0.md` | NOT DONE (file does not exist) |
| 6 | A runner that writes a rankings JSONL (`rankings-format.md`) from the Python retriever for a chosen profile on the test split | NOT COMMITTED. `scribe-eval score` consumes rankings but nothing in the repo emits them for arbitrary profiles from `/retrieve` (the gate does this internally for `hybrid-rrf-rerank` on validation only). Writing this runner is part of T17 |

## 2. Commands to produce the numbers (run by the owner, in this order)

```bash
docker compose up -d db
createdb -h localhost -p 54329 -U scribe scribe_final_test            # password: scribe
export DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/scribe_final_test
scripts/apply-migrations.sh "$DATABASE_URL_TEST"
export GOOGLE_GENERATIVE_AI_API_KEY=...  VOYAGE_API_KEY=...           # shell only

# index the corpus with the real embedder into the eval workspace
uv run --project intelligence scribe-eval ingest-corpus --corpus fixtures/corpus \
  --embedder gemini --database-url "$DATABASE_URL_TEST" --reset

# produce R.jsonl for the chosen profile on the TEST split (runner: see precondition 6), then:
uv run --project intelligence scribe-eval score --rankings R.jsonl \
  --dataset benchmarks/datasets/golden-v1 --split test --profile <chosen> --milestone-report \
  --model embedding=gemini-embedding-2 --model rerank=<voyage model id>

# the same for the Scribe 1.0 baseline rankings (scripts/eval-baseline.ts output), then compare:
uv run --project intelligence scribe-eval compare <run dir A> <run dir B>

# answer-level scoring (generation + citation + groundedness), test split, also --milestone-report:
uv run --project intelligence scribe-eval answer --rankings R.jsonl --split test --profile <chosen> --milestone-report
```

## 3. Report skeleton

Fill this in from run records only. Copy numbers; never retype them from memory.

Run record ids (INV-11): dataset version NOT MEASURED, profile hash NOT MEASURED, commit NOT MEASURED, model ids NOT MEASURED.

### 3.1 Retrieval, test split, answerable cases

| System | n | Recall@5 | Hit@5 | MRR | nDCG@10 | P@5 |
|---|---|---|---|---|---|---|
| Scribe 1.0 (threshold 0.3) | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED |
| Scribe 1.0 (no threshold) | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED |
| Scribe 2.0 chosen profile | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED |

Paired bootstrap difference (2.0 minus 1.0) with 95% interval: NOT MEASURED.

### 3.2 By category (10 categories)

NOT MEASURED.

### 3.3 Abstention (unanswerable cases)

Missed abstentions, `tau` used: NOT MEASURED. (`tau` is uncalibrated 0.0 in the repo today.)

### 3.4 Answer quality and verification

Correctness, citation precision/recall, groundedness, judge agreement kappa on 60 human-labelled answers, false-VERIFIED and false-UNSUPPORTED rates: NOT MEASURED.

### 3.5 Latency and cost

p50/p95 retrieval latency, embedding and rerank cost per query: NOT MEASURED against real providers. (Synthetic database-only timings exist in `performance.md`; they are not these.)

### 3.6 Worked failures (at least 5 required by the plan)

NOT MEASURED. Use `/eval/[runId]` after `scribe-eval publish-run` (see `T30-notes.md`).

### 3.7 Caveats to write after the run

Review state of the golden set, corpus is synthetic Northwind data, sample size per category, deviations from the commands above.

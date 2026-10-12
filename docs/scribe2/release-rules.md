# Release rules: the retrieval regression gate (T31)

A pull request must not make retrieval worse. `scribe-eval gate` (CI job `retrieval-gate`) enforces the rules below on the **validation** split of the golden set, using **cached** provider responses only. Implementation: `intelligence/src/scribe_eval/gate.py`. Refresh procedure: `benchmarks/gate/README.md`.

## What the gate does

1. Indexes the corpus (`fixtures/corpus`) into two throwaway workspaces of the CI test database: the eval workspace and a **decoy** workspace holding the identical documents. Both use the real indexer; document embeddings come from the cache.
2. Runs every validation case through the real retrieval strategy (default profile `hybrid-rrf-rerank`: dense + lexical, RRF, Voyage rerank, dedup) inside the eval workspace. Query embeddings and rerank responses come from the cache.
3. Scores the top 20 chunks per case with the normal metrics (D17 matching) and compares with the committed baseline `benchmarks/gate/baseline.json`.
4. Deletes both throwaway workspaces.

**Replay, never providers.** The embedder is built with no API key and a blocked HTTP transport, the reranker is a stub that raises, and provider keys are removed from the process environment before anything runs. The CI job sets none. A **cache miss fails the gate** with a message telling the developer to refresh the cache locally with their own keys. Anything that changes what the providers would be asked (chunking, document text, case questions, the candidate pool, fusion order or the document text sent to the reranker) therefore needs a refreshed cache in the same PR. That is intentional: it forces the author to look at a real score.

## The rules

Let `delta = current - baseline`, per answerable case, on the same case ids.

| Rule | Condition to PASS | FAIL when |
|---|---|---|
| **R1a** Recall@5 | 95% paired-bootstrap interval of `mean(delta recall@5)` is not entirely below 0 | **the interval's upper bound is < 0** |
| **R1b** MRR | same for `mrr` | **the interval's upper bound is < 0** |
| **R2** Scoping | cross-workspace hits == 0 | any chunk of any case's retrieved list (fused candidates included, not only the top 20) belongs to a workspace other than the eval workspace. Absolute, not relative to the baseline. |
| **R3** Abstention | missed abstentions <= baseline | an unanswerable case that the baseline abstained on is now answered, i.e. the count of missed abstentions increased |
| **P** Preconditions | same split, dataset sha256, profile name and case ids as the baseline; no document that the baseline indexed now fails to index | any mismatch. If it is legitimate (dataset edited, profile renamed) the PR refreshes the baseline and the diff is reviewed. |

Details:

- **Bootstrap.** Same code as `scribe-eval compare`: resample case ids with replacement 10,000 times, fixed seed 20260101, percentile interval [2.5%, 97.5%] of the mean of `current - baseline`. Identical rankings give `[0, 0]` and pass. A mean drop whose interval still contains 0 is printed as `WARN` and passes (it is indistinguishable from noise at this sample size).
- **Missed abstention.** For an unanswerable case the gate abstains iff there is no ranking score or `top rerank score < tau`; otherwise the abstention is missed. `tau` is the profile's answerability threshold (`GateConfig.tau`). **It is currently the uncalibrated placeholder 0.0, which rejects nothing, so R3 is weak until T17 calibrates it** (the report says so). The baseline stores the `tau` it was measured with; a different `tau` is printed in the R3 line.
- **Cross-workspace hits** reuse the scoping contract tested in `tests/retrieval/test_scoping.py` (INV-2/INV-3): the retrievers must filter by workspace inside the SQL. The gate checks the output side on the real eval corpus: each returned chunk id is looked up and must belong to the eval workspace, with an identical-content decoy workspace present so a leak would surface instead of hiding behind uniqueness.

## Known limits (be honest about what a green gate means)

- **Power.** With ~40 validation cases a true drop of a few points usually has an interval that contains 0, so it passes with a WARN. R1 catches statistically evident regressions, not small ones. Growing the validation split (T28 added unreviewed cases) tightens it.
- **Unreviewed data.** The golden set is not yet human-reviewed (`benchmarks/datasets/golden-v1/REVIEW_FIRST.md`). The gate measures change relative to the baseline, so it is meaningful before review, but the baseline numbers must not be quoted as quality figures (INV-12 still bars the test split, which the gate refuses to score).
- **Scanned PDF.** OCR responses are not cached, so the scanned PDF fails to index in both the baseline and replay. It is recorded in the baseline's `failed_docs`; cases that need it count as misses on both sides.
- **Rerank in the path.** A change to fusion changes what is sent to the reranker, so replay raises a cache miss (still a FAIL, with a refresh message). Use `--profile hybrid-rrf` (no rerank) to see a pure metric comparison of fusion changes without keys.
- **Not a latency or cost gate.**

## GATE NOT ARMED

The CI job exits 0 with a warning annotation `GATE NOT ARMED` (and a banner in the log) until `benchmarks/gate/baseline.json` exists and is a **real** baseline: `measurement: true`, `seeded: false`, no fake model ids. A seeded or fake baseline never arms the gate. The step that prints this also skips `npm ci` and the migrations, so an unarmed job is quick.

## Arming the gate (what the owner runs, locally, once)

Needs `GOOGLE_GENERATIVE_AI_API_KEY` and `VOYAGE_API_KEY`, Docker (`docker compose up -d db`) and a throwaway database whose name contains `test`.

```bash
docker compose up -d db
createdb -h localhost -p 54329 -U scribe scribe_gate_test        # password: scribe
export DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/scribe_gate_test
scripts/apply-migrations.sh "$DATABASE_URL_TEST"
export GOOGLE_GENERATIVE_AI_API_KEY=...  VOYAGE_API_KEY=...       # never commit these
uv run --project intelligence scribe-eval gate --refresh-cache --database-url "$DATABASE_URL_TEST"
unset GOOGLE_GENERATIVE_AI_API_KEY VOYAGE_API_KEY
uv run --project intelligence scribe-eval gate --write-baseline --database-url "$DATABASE_URL_TEST"
uv run --project intelligence scribe-eval gate --database-url "$DATABASE_URL_TEST"   # must PASS against its own baseline
git add benchmarks/gate/cache.jsonl.gz benchmarks/gate/baseline.json && git commit
```

Do this after the golden-set review so the baseline is on reviewed data. Expected cost: the corpus embeddings plus the validation queries and rerank calls (one pass); expected size of the committed cache: a few MB (vectors rounded to 6 decimals, gzip).

# Retrieval gate data (T31)

This directory holds the two files the CI gate needs. Neither exists yet, so the gate reports `GATE NOT ARMED` (CI passes) until both are committed. Rules and the full procedure: `docs/scribe2/release-rules.md`.

| File | What | Produced by | Size |
|---|---|---|---|
| `cache.jsonl.gz` | Cached Gemini embedding + Voyage rerank responses for the corpus, the validation questions and the rerank inputs the default profile produces. Sorted keys, fixed gzip header (stable diffs). | `scribe-eval gate --refresh-cache` (LOCAL ONLY, needs keys, refused in CI) | a few MB expected (vectors rounded to 6 decimals; only keys the run used are kept) |
| `baseline.json` | Per-case Recall@5/MRR/etc., failed documents, missed abstentions, tau, model ids, dataset sha256, commit. | `scribe-eval gate --write-baseline` (replays the cache, no keys) | tens of KB |

## Refresh the cache (after changing chunking, parsers, retrieval inputs, or the dataset)

```bash
export DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/<throwaway db containing "test">
export GOOGLE_GENERATIVE_AI_API_KEY=... VOYAGE_API_KEY=...        # your keys, shell only
uv run --project intelligence scribe-eval gate --refresh-cache --database-url "$DATABASE_URL_TEST"
```

## Refresh the baseline (only when the new numbers are an intended, reviewed change)

Review every case in the selected split first (`scribe-eval review`); `--write-baseline` refuses cases without `reviewed: true`. The baseline records this review check, profile configuration hash, model IDs and abstention settings. A mismatch or missing/nonfinite case metric fails comparison. Changing the acceptance bar still requires a reviewer to inspect the baseline/cache diff; CI does not grant that approval.

```bash
unset GOOGLE_GENERATIVE_AI_API_KEY VOYAGE_API_KEY
uv run --project intelligence scribe-eval gate --write-baseline --database-url "$DATABASE_URL_TEST"
git diff benchmarks/gate/baseline.json      # the reviewer reads this: it is the bar the next PRs must clear
```

Refreshing the baseline in a PR is how a deliberate trade-off is recorded; the diff is the audit trail. Never edit the baseline by hand, and never write one from fake models (the command refuses). Seeded test fixtures live under `intelligence/tests/evaluation/` and are stamped `SEEDED FIXTURE - NOT A MEASUREMENT`.

GitHub CI retains status, scoring results when available and error logs in its `retrieval-gate-<attempt>` artifact for 14 days. The missing real baseline still leaves the gate explicitly unarmed.

## Run the check yourself

```bash
unset GOOGLE_GENERATIVE_AI_API_KEY VOYAGE_API_KEY
uv run --project intelligence scribe-eval gate --database-url "$DATABASE_URL_TEST"   # exit 0 pass, 1 fail
```

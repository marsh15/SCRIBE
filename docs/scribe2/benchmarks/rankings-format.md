# Rankings file format

`scribe-eval score --rankings <file>` takes a JSONL file with one line per case and items ordered best first:

```json
{"case_id": "sf-01",
 "ranking": [{"chunk_id": "c1", "doc": "onboarding-handbook.pdf", "text": "chunk text", "score": 0.83, "method": "dense"}],
 "latency_ms": 123.4, "cost": {"embed_usd": 0.0001}, "degraded": false}
```

- `case_id` and `ranking` (a list, which may be empty) are required. `chunk_id`, `doc`, `score`, `method`, `latency_ms`, `cost` and `degraded` may be null or absent. Each item needs `text`.
- Every case of the requested split must be present; a missing id is an error. Duplicates are an error. Extra ids are ignored.
- Relevance (D17): an item is relevant to an evidence entry if and only if `normalize(text)` contains `normalize(quote)`.
- Metrics: recall@1/5/10 is matched evidence entries in the top k divided by all entries. hit@k is the any-match version. MRR is 1/rank of the first relevant item. nDCG@10 uses grades 1/2 (each entry is credited to its first matching item, and the ideal ranking sorts grades descending). precision@5 is relevant items in the top 5 divided by 5. Unanswerable cases are excluded and reported separately with the top score (if any).
- Runs are written to `<out>/<run_id>/run.json` and `results.jsonl` (default `benchmarks/runs`).
- `--split test` requires `--milestone-report` (INV-12).

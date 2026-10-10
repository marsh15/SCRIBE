# Rankings file format

Input to `scribe-eval score --rankings <file>`. JSONL, one line per case, items ordered best first:

```json
{"case_id": "sf-01",
 "ranking": [{"chunk_id": "c1", "doc": "onboarding-handbook.pdf", "text": "chunk text", "score": 0.83, "method": "dense"}],
 "latency_ms": 123.4, "cost": {"embed_usd": 0.0001}, "degraded": false}
```

- `case_id`, `ranking` (list, may be empty) required. `chunk_id`, `doc`, `score`, `method`, `latency_ms`, `cost`, `degraded` may be null/absent. Each item needs `text`.
- Every case of the requested split must be present (missing ids are an error); duplicates are an error; extra ids are ignored.
- Relevance (D17): an item is relevant to an evidence entry iff `normalize(text)` contains `normalize(quote)`.
- Metrics: recall@1/5/10 = matched evidence entries in top k / all entries; hit@k = any; MRR = 1/rank of first relevant item; nDCG@10 with grades 1/2 (each entry credited to its first matching item, ideal = grades sorted descending); precision@5 = relevant in top 5 / 5. Unanswerable cases are excluded and reported separately with the top score (if any).
- Runs are written to `<out>/<run_id>/run.json` and `results.jsonl` (default `benchmarks/runs`).
- `--split test` requires `--milestone-report` (INV-12).

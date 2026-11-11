# Frozen copy of golden-v1 (state before the human review began)

`cases.jsonl`, `meta.json`, `REVIEW_FIRST.md` and `UNREVIEWED_T28.md` are byte-for-byte the files from `benchmarks/datasets/golden-v1` on `main` at 1065285: 200 cases, 80 machine-drafted, none reviewed.

`tests/evaluation/test_review_tool.py` and `tests/evaluation/test_t28_answer.py` read this copy, not the live dataset. The live dataset changes as the owner reviews and drops cases, and those tests assert the original shape (200 cases, nothing reviewed). Do not edit these files; review decisions belong in `benchmarks/datasets/golden-v1`.

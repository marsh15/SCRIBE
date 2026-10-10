# golden-v1: Scribe 2.0 retrieval golden set

**Review status: NOT YET HUMAN-REVIEWED.** A person must verify every case (question, reference answer, evidence quotes, forbidden docs) against the corpus in `fixtures/corpus/` before this dataset is used for milestone reports.

120 questions over the 24-document Northwind Logistics corpus (`fixtures/corpus/`, planted facts in `fixtures/corpus/FACTS.md`), 12 per category. Files: `cases.jsonl`, `meta.json`.

## Categories

| Category | Id prefix | Definition |
|---|---|---|
| single_fact | sf | One fact stated in one prose passage. |
| table_lookup | tb | Answer lives in one table row (CSV, PDF table, DOCX table). |
| identifier_exact | id | Query contains an exact identifier (error code, ticket, SKU, plan or contract number) where lexical match matters. |
| numeric_reasoning | nr | Requires computing or comparing numbers or dates; the evidence holds every input. |
| cross_document | cd | Needs two or more documents; evidence lists a quote from each. |
| version_conflict | vc | Contradicting documents (leave policy 2024 vs 2026, expense policy vs travel guidelines, stale handbook). The question pins one version (and `forbidden` lists the other) or asks what changed (evidence has both quotes). |
| exception_qualified | eq | A carve-out qualifies a general rule. Exception quote is grade 2, rule quote grade 1. |
| paraphrase_semantic | ps | Wording shares almost no keywords with the source. |
| disambiguation | dm | Near-duplicate sentences or identical paragraphs in different documents; the question gives enough context to choose and `forbidden` lists the lookalike. Includes cases on the scanned PDF (tag `scanned`). |
| unanswerable | ua | `answerable=false`, no evidence. 8 target topics deliberately absent from the corpus; 4 are plausible questions near present documents whose specific detail is not stated. |

## Schema (one JSON object per line in `cases.jsonl`)

`id`, `split` (`dev`|`validation`|`test`), `category`, `tags[]`, `question`, `answerable`, `reference_answer` (1-3 sentences), `must_include[]` (short strings a correct answer must contain; empty when unanswerable), `evidence[]` of `{doc, version, quote, page, grade}`, and `forbidden[]` of `{doc}`. The authoritative definition is `intelligence/src/scribe_eval/dataset.py`.

- `doc`: filename in the corpus. `version`: 1 for every document here.
- `quote`: at most 200 characters, copied verbatim, within one sentence or one table row, occurring exactly once in that document after normalization.
- `page`: real PDF page (1-based) for PDFs, `null` otherwise. The scanned PDF is one page.
- `grade`: 2 = directly answers, 1 = supporting (a needed rule, an input, or context).
- `forbidden`: documents whose chunks are wrong for this question (the other policy version, the lookalike sentence).

## How labels are matched (DESIGN D17)

Labels are profile-independent: `(doc, version, quote, page, grade)`. A retrieved chunk is relevant if the normalized text of the chunk contains the normalized quote (`scribe_eval.normalize.normalize`: NFKC, curly quotes and dashes to ASCII, lowercase, whitespace collapsed). The same labels therefore score the legacy parser, new parsers and future parsers. A chunk from a `forbidden` doc counts as a wrong-version hit. Scanned-PDF quotes are checked against `facility-safety-notice-scanned.truth.txt`, since the PDF has no text layer.

## Split policy (INV-12)

Splits are assigned per category (stratified) with `scribe-eval assign-splits benchmarks/datasets/golden-v1 --seed 20260101`, ratios in `meta.json` (dev 0.4, validation 0.2, test 0.4). Resulting counts: 50 dev, 20 validation, 50 test. Tuning and debugging use `dev`; model and parameter selection use `validation`. The `test` split is used only for milestone reports and is never used to tune anything. Do not edit cases after looking at test results; add new cases to a new dataset version instead.

## Validation

`uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1` checks schema, unique ids, quote presence and uniqueness, forbidden docs, split coverage and category balance.

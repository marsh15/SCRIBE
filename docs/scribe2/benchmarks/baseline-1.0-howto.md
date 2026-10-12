# How to run the Scribe 1.0 frozen retrieval baseline (T07)

This procedure produces the numbers every Scribe 2.0 profile is compared against. It measures the CURRENT TypeScript pipeline as it is
(`lib/ingestion/extract.ts`, `lib/chunking.ts`, `lib/embeddings.ts`, `lib/search.ts`) and changes none of it.
The script is `scripts/eval-baseline.ts`. Whoever runs it needs a database and `GOOGLE_GENERATIVE_AI_API_KEY`.

## What it does

1. Refuses to run unless the target is explicitly an eval database (see Safety).
2. Creates or reuses ONE workspace (`kind='eval'`, owner `eval-golden-v1`) and ingests the 24 documents of `fixtures/corpus/`
   (not `FACTS.md`, not `*.truth.txt`, which are labels) through extract, chunk (4000/400), embed
   (`models/gemini-embedding-2-preview`, 3072 dims), and `files`/`documents` inserts shaped like `lib/ingestion/source-intake.ts`.
   Documents that fail extraction get a `files` row with `status='failed'` and no chunks, which is what production would do.
3. For every golden-v1 case (all splits), retrieves the top 10 twice:
   `rankings.threshold-0.3.jsonl` (`searchDocuments(q, user, workspace, 10, 0.3)`, as chat calls it) and
   `rankings.threshold-none.jsonl` (threshold -1, nothing filtered).
4. Writes `baseline-meta.json` (dataset version, git commit, model and dimensions, chunker parameters, variants, driver,
   per-document ingestion stats and failures, node version).

Embeddings are cached in `benchmarks/cache/ts-embeddings.jsonl` (git-ignored; key `sha256(model + sanitized text)`), so a rerun or the
second threshold variant costs no API calls. Delete the file to force fresh embeddings.

## Safety rules enforced by the script

- `--database-url URL` or `EVAL_DATABASE_URL` is mandatory. `NEON_DATABASE_URL` is never used, and a URL equal to it (shell or `.env.local`) is refused.
- `EVAL_ALLOW_WRITE=1` is mandatory.
- A database where any `personal` workspace owns files is refused. If you deliberately use a Neon branch of production, set
  `EVAL_ALLOW_POPULATED_DB=1`; every write and delete is still scoped to the eval workspace (the integration test proves a decoy personal workspace is untouched).
- The workspace used must be `kind='eval'` with owner `eval-golden-v1`, otherwise the script stops.
- `--reset` deletes only that workspace's `documents` and `files`. Without `--reset`, an already-populated eval workspace is refused.

## Prerequisites

- **Node 18.16 is known to work. Node 26 does NOT.** `pdf-parse` bundles pdf.js 1.10, which corrupts its own state across sequential
  parses on Node 26 and fails valid PDFs ("bad XRef entry", "Illegal character: 41"). The live run aborts if it sees those errors, because they would
  measure Node, not Scribe. Node 20, 22 and 24 were not tested, so check with `--extract-only` first (below). Use production's Node version if it parses all PDFs.
- The schema must exist (the `workspaces`, `files` and `documents` tables). A Neon branch of production has it. For a local DB, run
  `bash scripts/apply-migrations.sh postgresql://scribe:scribe@localhost:54329/baseline`, which drops and recreates schema `public`, so use a DB dedicated to this.
- `psql` on PATH for `--driver psql`.

## Steps

```bash
# 0. Check the offline stage on your Node (no DB, no key, no cost). Expect 24 docs, 21 extracted, 23 chunks, 3 failures,
#    pdf_parser_state_failures: [].
npx tsx scripts/eval-baseline.ts --extract-only | head -12

# 1. A database. Either a Neon branch (create it from the Neon console or CLI; copy its connection string)
#    or the local docker one:
#      docker exec scribe-db-1 psql -U scribe -d scribe -c 'CREATE DATABASE baseline'
#      bash scripts/apply-migrations.sh postgresql://scribe:scribe@localhost:54329/baseline

# 2. Environment, in your shell only. Never commit these, never put the eval URL into NEON_DATABASE_URL.
export EVAL_DATABASE_URL='postgresql://...'      # the Neon branch / local DB
export EVAL_ALLOW_WRITE=1
export GOOGLE_GENERATIVE_AI_API_KEY='...'        # or in .env.local (only this key is read from it)
# export EVAL_ALLOW_POPULATED_DB=1               # only for a branch of production data

# 3. Real driver: the real lib/search.ts over the Neon HTTP driver. Writes benchmarks/runs/baseline-1.0/.
npx tsx scripts/eval-baseline.ts --driver neon --reset

# 4. Same SQL through psql (works on local DBs; use it to cross-check the neon run). Different --out, same cache.
npx tsx scripts/eval-baseline.ts --driver psql --reset --out benchmarks/runs/baseline-1.0-psql
```

`--driver neon` needs a Neon (HTTP) endpoint and cannot talk to the local Docker Postgres. `--driver psql` runs the same SQL text, produced by
drizzle's `toSQL()`. `__tests__/eval-baseline.test.ts` asserts that this text equals the query `lib/search.ts` builds (recorded through a mocked `db`).
The two drivers should give identical rankings. If they differ, stop and investigate before using either.

`--limit N` runs the first N cases (smoke test). Do not use a limited run as the baseline.

## Score it

```bash
for v in threshold-0.3 threshold-none; do
  for s in dev validation; do
    uv run --project intelligence scribe-eval score \
      --rankings benchmarks/runs/baseline-1.0/rankings.$v.jsonl \
      --dataset benchmarks/datasets/golden-v1 --split $s \
      --profile baseline-1.0-$v \
      --model embedding=models/gemini-embedding-2-preview \
      --out benchmarks/runs
  done
done
```

Do NOT score `--split test` here. The script retrieves for every case, so the test split's ranking lines exist, but INV-12 allows
scoring that split only for a milestone report (`--milestone-report`).

`benchmarks/runs/` is git-ignored. The committed record is `docs/scribe2/benchmarks/baseline-1.0.md` (below). Copy
`baseline-meta.json` into it (or `git add -f` it) so the commit hash, model and dimensions stay attached to the numbers (INV-11).

## Cost estimate for the live run

In the offline stage (Node 18.16), 21 documents extract to **23 chunks** in total (fleet-inventory.csv and onboarding-handbook.pdf have 2 each, the other 19 have 1).

| Step | Requests | Notes |
|---|---|---|
| Document embeddings | 21 batch requests, 23 texts | `generateEmbeddings` batches per file, up to 100 per request. 23 texts would fit in 1 request, but ingestion is per file |
| Query embeddings | 120 single requests | one per case; the second threshold variant hits the cache |
| Total | about 141 requests, 143 embeddings | a few thousand input tokens for documents and a few thousand for queries; negligible cost |

A rerun costs 0 requests. In each ranking line, `cost.embedding_calls` is the number of real API requests that case triggered (0 for the second variant, which reuses the cached query embedding).

## What to put in `docs/scribe2/benchmarks/baseline-1.0.md`

Create this file after the run; it does not exist yet. It needs these sections:

1. **Header (INV-11)**: dataset version `golden-v1` and its review status (it says NOT YET HUMAN-REVIEWED unless that changed), git commit,
   embedding model id `models/gemini-embedding-2-preview` and dimensions 3072, chunker 4000/400 `RecursiveCharacterTextSplitter`, retrieval function and top-k 10,
   threshold variants, driver(s), Node version, run date.
2. **Overall results**: the `scribe-eval score` table for dev and for validation, for each threshold variant (paste the tables).
3. **Per-category results**: the ten categories (single_fact, table_lookup, identifier_exact, numeric_reasoning, cross_document, version_conflict,
   exception_qualified, paraphrase_semantic, disambiguation, unanswerable) with recall@1/5/10, MRR, nDCG@10, plus the unanswerable top-score summary.
4. **Per-format results**: group cases by their `tags` (pdf, docx, csv, md, txt, scanned) and report the same metrics per group.
5. **Ingestion**: documents extracted vs failed, chunks per document (from `baseline-meta.json`), the failures and their error text.
6. **Latency and cost**: median and p95 `latency_ms` per variant, embedding requests (ingestion and queries).
7. **Known measurement artifacts** (below), each with the cases it affects.
8. **Reproduction**: the exact commands used, and the cache and workspace notes.

## Known measurement artifacts

List these in the report.

- **CSV rows are flattened by joining cells with spaces** (`extract.ts`: `row.join(" ")`). Evidence quotes copied as raw lines (commas, quotes,
  semicolons) cannot match under D17 normalization for `benefits-table.csv` and `fleet-inventory.csv`, so those table cases can score 0 on relevance even when the right chunk is retrieved.
- **The scanned PDF fails extraction** (`facility-safety-notice-scanned.pdf`: "No text extracted ... Scanned PDFs ... are not supported yet"). Its cases (tag `scanned`) cannot be answered by the 1.0 pipeline; this is a baseline finding, not a harness fault.
- **Two CSVs fail extraction** under the strict `csv-parse` defaults: `office-locations.csv` (ragged rows: "expect 7, got 6 on line 2") and `price-list-2026.csv` (semicolon-delimited with a BOM: "expect 1, got 2 on line 2"). Cases that depend on them (price list, office lookups) are unanswerable for 1.0.
  Together with the scanned PDF, that is 3 of 24 documents missing from the index.
- **Node-version dependence of PDF extraction** (above): results from a Node version on which pdf-parse fails are invalid. Record the Node version.
- **Near-whole-document chunks.** The corpus is small (most documents are under 4000 characters), so almost every document is one chunk and the whole index is 23 chunks. Top-10 therefore covers nearly half the index, recall@10 saturates, and chunk-level precision is low by construction. These numbers are not directly comparable with those of a structural chunker that yields many small chunks.
- **Relevance is substring-based (D17).** A whole-document chunk "contains" every quote in its document, including quotes from the wrong version when two policy versions share wording; version_conflict and disambiguation cases are judged through `forbidden`, not through the chunk-contains-quote test alone.
- **Queries are the raw case question.** Production chat may rewrite the query or issue tool calls with different text; the baseline embeds the question verbatim.
- **Latency is not production latency.** It includes the embedding API round trip and, for `--driver psql`, spawning `psql` and parsing a large literal. The second variant's latency excludes the query embedding (a cache hit). Report latency as indicative only.
- **Preview embedding model.** `gemini-embedding-2-preview` can change without notice; the embedding cache pins what was measured, so keep `benchmarks/cache/ts-embeddings.jsonl` (or its checksum) with the run.
- **Dataset not yet human-reviewed.** Any label error shows up as a baseline error. Note the review status in the header.
- **The eval workspace is the only data indexed**, so there are no distractors from other users' files. Production retrieval has per-user scope but a bigger pool per user.

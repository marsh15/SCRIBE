# Distractor corpus for golden-v1 (corpus-large)

## Why

The 24 gold documents are tiny: the legacy 4000/400 chunker turns them into about 23 chunks, so top-10 covers nearly half of the
index and recall@10 saturates. A retriever cannot be told apart from another one on that index. The distractor corpus adds 300
more Northwind Logistics documents (about 1,900 legacy chunks) on topics that are disjoint from the gold documents, in the same genres and
house style, so that retrieval is genuinely hard while no golden label changes meaning.

Gold documents, `cases.jsonl` and `FACTS.md` are not touched. The distractors live in `fixtures/distractors/` (git-ignored; regenerated).
Only `fixtures/distractors.manifest.json` is committed.

## Contents

| Format | Docs | Notes |
|---|---|---|
| PDF | 60 | 2 to 12 pages, repeated header and footer on every page, real headings, pipe-text tables |
| DOCX | 60 | Heading styles, real tables, header and footer |
| CSV | 45 | 40 to 400 rows; comma, semicolon (decimal comma), tab and pipe delimiters; 9 with BOM; 6 without a header row; 3 deliberately ragged (`...-carrier-scorecard-018.csv`, `...-price-changes-030.csv`, `...-parking-permits-045.csv`) |
| Markdown | 70 | API specs with error tables, runbooks, policies, FAQs, minutes, post-mortems, glossaries |
| TXT | 65 | Changelogs, post-mortems, glossaries, minutes, policies, notices |

31 topics (cafeteria and catering, parking and shuttle, IT asset lifecycle, marketing campaigns, training catalogue, depot shift rosters,
supplier onboarding for suppliers other than Helios, warehouse slotting, pallet standards, returns processing, fuel card reconciliation,
EV charging pilot, internal audit checklists, procurement approvals, facilities maintenance, driver licensing, customer complaints,
peak and closure calendars, unrelated project status reports, knowledge-base how-tos, consumables price lists, sensor calibration, packaging waste,
visitor registration, mail room, workshop and tyres, handling equipment inspection, internal communications, master data quality,
carrier subcontracting, site energy metering). Genres: policies with numbered sections, SLAs and agreements with clauses and credit tables,
API specs with error tables, runbooks, minutes, changelogs, post-mortems, reports, FAQs, glossaries, registers, checklists, course catalogues, price lists.

Hard negatives: the same register as the gold documents, plus lexical near-misses (neighbouring identifiers such as `ERR_AUTH_404`, `INV-2048`,
`VND-8813`, `INC-2025-118`, `NW-T-0418`, `DPA`-style and `SEC-` style references, other versions and thresholds written with the same sentence shape).

## Build, assemble, verify

```bash
# 1. Generate (deterministic, about 45 s) and write fixtures/distractors.manifest.json
uv run --no-project --with reportlab --with python-docx --with pillow --with pypdfium2 python fixtures/build_distractors.py

# 2. Verify against the golden set and the manifest (fails non-zero with a list of violations)
uv run --project intelligence python -m scribe_eval.distractor_check \
    --distractors fixtures/distractors --dataset benchmarks/datasets/golden-v1 \
    --manifest fixtures/distractors.manifest.json

# 3. Copy gold documents + truth sidecar + distractors into fixtures/corpus-large/ (FACTS.md excluded)
python3 fixtures/assemble_large_corpus.py
```

`distractor_check` prints the document count and size by format, an estimate of the legacy 4000/400 chunk count per format
(exact via `scribe_ingest.legacy_chunker` on this machine) and the structural chunk count. The structural figure is an ESTIMATE: it parses and
chunks a sample of at most 40 documents and scales the per-format average up. `--no-chunks` skips both estimates (quick pass).
`uv run --project intelligence pytest tests/evaluation/test_distractor_check.py` covers every rule with synthetic documents and also runs the real directory when present.

### Use it

Point the offline stage, the baseline run and ingestion at the large corpus with the one flag `--corpus fixtures/corpus-large`:

```bash
# T07 baseline, offline extract/chunk stage (Node 18 for pdf-parse, see baseline-1.0-howto.md)
PATH=/usr/local/bin:$PATH npx tsx scripts/eval-baseline.ts --extract-only --corpus fixtures/corpus-large
# T07 baseline, live (database, key); same flag
npx tsx scripts/eval-baseline.ts --driver neon --reset --corpus fixtures/corpus-large --out benchmarks/runs/baseline-1.0-large
# Scribe 2.0 index
uv run --project intelligence scribe-eval ingest-corpus --corpus fixtures/corpus-large ...
```

Use a separate `--out` / profile name for large-corpus runs: its numbers are not comparable with the 24-document runs (`baseline-1.0`), and
the report header must say `corpus: fixtures/corpus-large (24 gold + 300 distractors)` (INV-11). Both tools skip `FACTS.md` and `*.truth.txt` as documents.
Offline expectation on Node 18.16: 324 documents, 316 extracted, 8 failed, about 1,920 legacy chunks. The 8 failures are the 3 known gold failures
(scanned PDF, `office-locations.csv`, `price-list-2026.csv`) plus 5 distractor CSVs that the strict `csv-parse` defaults reject (the 3 ragged ones and 2 non-comma-delimited
files with decimal commas). They are intentional legacy edge cases; the new parser reads all 300.

## Constraints and how they are enforced

A distractor must never change what a golden label means. The generator regenerates a document (next seeded attempt) when it violates a rule, and
`scribe_eval.distractor_check` re-verifies the written files (PDF and DOCX text is read back with the same loaders as `scribe_eval.corpus`):

* (a) **No gold evidence quote.** Normalized containment (`scribe_eval.normalize`, D17) of every `cases.jsonl` quote, whole document, and for CSV quotes also the ordered-cells rule of `scribe_eval.metrics`.
* (b) **No `must_include` string of an answerable case**, matched as whole words/numbers. Not enforceable, and therefore exempt: bare integers and bare one-decimal numbers
  (`2`, `30`, `1.3`, `16.5`; they occur in every document as quantities and section numbers), the generic words `No`, `not`, `not found`, `Director`, `CEO`, `maintenance`, `hub`, `highest`,
  `entertainment`, `does not apply`, and the prefix `SEC-` (neighbouring `SEC-nnnn` tickets are an intended near-miss). Everything else is enforced, including
  `30 minutes`, `24 hours`, `10 working days`, `09:00`, `2026-04-09`, `6.5%`, `EUR 45`, `Northwind Logistics B.V.`, `Dutch law`, `proof of delivery`, `Tuesdays`, `Helios`.
  Consequences for the text: no `B.V.` (the PDF/DOCX footer of the gold renderer is rewritten to `Northwind Logistics - Confidential - Internal use only`), no depot codes
  (`D-RTM` ...), no `Point B`, no `HR portal`, no `TLS certificate`, and times, dates and durations are drawn from pools without the golden values.
* (c) **No exact gold identifier** (tokens from `FACTS.md`, the dataset, and every identifier-shaped token in the text files of `fixtures/corpus`, including all fleet ids and plates; whole-token match,
  so `NW-T-0418` is allowed and `NW-T-0417` is not), **and none of the 15 absent-topic patterns** (`stock`, `contractor`, `relocat`, `salar`, `pension`, `uniform`, `emission`, `duty`, `commission`, `smok`, ...; read from `FACTS.md` too). This forces the vocabulary:
  `inventory` instead of stock, `external provider` instead of contractor, `rate card` instead of tariff, `start-up` instead of commissioning, and so on.
* (d) **No file name shared with a gold document** (case-insensitive), nor `FACTS.md` / `*.truth.txt`.
* **Soft avoid** (generator only, not a hard rule): phrases that would give an unnamed golden question a plausible competing answer, for example `base URL`, `rate limit`, `webhook`, `retries`,
  `per page`, `degrees`, `damaged`, `proof of delivery`, `last day`, `reimburse`, `expense`, `rack load`, `evacuate`, `hotel`, `control room`, `sick`, `annual leave`. Distractors therefore talk about
  other things in the same genre (for example "throttled above N calls per minute" and "Gateway root" instead of "rate limit" and "Base URL").

Golden questions that name an entity (a document, a vendor, a contract number) are safe by construction. Unnamed ones were scanned by keyword overlap against every distractor sentence;
the nearest remaining lookalikes are listed in the build report and are deliberate near-duplicate sentence shapes with different numbers (liability caps for breach of a confidentiality clause, disputed
invoices without late interest, initial term clauses), never the gold sentence.

## Determinism and the manifest

Every document draws from its own RNG seeded by `(SEED=20260410, file name, attempt)`; PDFs use reportlab invariant mode and DOCX zips use fixed timestamps (the helpers of `build_corpus.py`).
The output was byte-identical across two runs and across Python 3.12 and 3.14. `fixtures/distractors.manifest.json` lists every file (name, size, sha256, format) with the generator version, seed and
the reportlab / python-docx versions. `distractor_check --manifest` and `assemble_large_corpus.py` fail on any missing, extra or changed file. Text formats (MD, TXT, CSV) are independent of library versions; PDF and DOCX
bytes may legitimately differ under another reportlab or python-docx version. If only those differ, regenerate and commit the manifest after the checker passes without `--manifest`.

## Files

* `fixtures/build_distractors.py`, `fixtures/distractor_src/` (word banks and templates): generator.
* `fixtures/assemble_large_corpus.py`: builds `fixtures/corpus-large/` (copies, no symlinks; git-ignored).
* `fixtures/distractors.manifest.json`: committed hashes.
* `intelligence/src/scribe_eval/distractor_check.py`, `intelligence/tests/evaluation/test_distractor_check.py`: checker and tests.

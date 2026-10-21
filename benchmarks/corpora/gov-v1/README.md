# gov-v1 corpus

A public-domain government document corpus for the Scribe 2.0 evaluation: revision pairs, old scans that exercise OCR, and table-heavy schedules and rate tables.

## Files

- `manifest.json`: the document list (id, title, agency, year, URL, licence basis, revision pair, pinned `sha256`, `ingest_file`, `derived`).
- `page-stats.json`: measured per-page stats and totals, computed on the files that are ingested.

The PDFs are **not committed**. They are fetched into `fixtures/corpus-gov/`, which is git-ignored:

```
uv run --project intelligence scribe-eval fetch-corpus \
  --manifest benchmarks/corpora/gov-v1/manifest.json --out fixtures/corpus-gov \
  [--pin] [--rasterize-ocr-layer]
```

Only the URLs in the manifest are requested. Every file must start with `%PDF`. The first run uses `--pin` to record each file's sha256 in the manifest; later runs verify the hash, skip files already present, and exit 1 on any mismatch. `--pin` only fills missing hashes; to deliberately re-pin a document, set its `sha256` to `null`.

## Where the documents come from and why they are free to use

| Source | Documents | Licence basis |
| --- | --- | --- |
| NIST / NBS (nvlpubs.nist.gov, csrc.nist.gov) | FIPS, SP, CSWP | US federal government work, 17 USC 105: no US copyright |
| IRS (irs.gov) | Publications 15-A, 15-B, 15-T | US federal government work, 17 USC 105 |
| NARA (archives.gov) | General Records Schedules | US federal government work, 17 USC 105 |
| FAA advisory circulars, via ROSA P (rosap.ntl.bts.gov, DOT National Transportation Library) | AC 20-42 / 20-100 / 20-101 series | US federal government work (17 USC 105); the ROSA P records state "Public Domain" |

Each manifest entry repeats its `licence_basis`. The protection under 17 USC 105 covers US federal works; the corpus makes no claim about use outside the United States.

## Removed documents

- `osha-3151-2023` (OSHA 3151, Personal Protective Equipment): removed, HTTP 403. The osha.gov URL refused both download attempts. No substitute was added.

- `nist-sp-800-61r2-2012` (SP 800-61 Rev. 2, 80 pages, 8 table-heavy): removed to meet the table-heavy target. `nist-sp-800-61r3-2025` stays in the corpus, unpaired.

## Added to reach the table-heavy target

Measured on download, then included: `irs-pub-15t-2022` (68 pages, 48 table-heavy), `nist-sp-800-53b-2020` (85, 42), `nara-grs-02-2` (7, 7), `nara-grs-02-3` (7, 7), `nara-grs-02-4` (7, 7), `nara-grs-05-6` (14, 13), `nara-grs-06-1` (5, 3). Together with `irs-pub-15t-2023` they are extra editions or standalone documents, not revision pairs (`revision_pair` is null; the `irs-15t` pair is 2024/2025).

## Measured, not needed

Downloaded and measured, then deleted from `fixtures/corpus-gov/` and left out of the manifest because they only dilute the table-heavy share:

| id | pages | scanned | table-heavy |
| --- | --- | --- | --- |
| `nara-grs-01-2` | 3 | 0 | 0 |
| `nara-grs-02-7` | 8 | 0 | 1 |
| `nara-grs-02-8` | 6 | 0 | 0 |

## Scanned documents and derived raster copies

The 1975-1988 NBS FIPS and 1977-1984 FAA ACs are page scans. Many carry a hidden OCR text layer, which makes a text parser see ordinary text and skips the OCR path that the evaluation wants to exercise.

A page counts as **scanned** under the ingest parser's rule (`scribe_ingest.pdf`): fewer than `OCR_CHAR_THRESHOLD` (25) non-space characters and at least one image. A page with an image and 25 or more characters is an **ocr_layer** page.

With `--rasterize-ocr-layer`, each document declared as an old scan (`scan_with_ocr_layer` set in the manifest) whose image pages mostly carry an OCR layer, and which has at most 50 pages (the OCR page cap), gets a derived copy `fixtures/corpus-gov/<id>.raster.pdf`:

1. every page rendered with pypdfium2 at 200 dpi, grayscale;
2. the images assembled into an image-only PDF with Pillow;
3. each derived page is checked to count as scanned under the ingest rule.

The manifest records `ingest_file` (`<id>.raster.pdf` for these) and `derived` = `{kind: "rasterized-image-only", dpi, source_sha256, sha256}`: the sha256 of the original download and of the derived copy. The original is kept next to it. Page stats are measured on the ingested file; `page-stats.json` keeps the original's counts under `original` for the rasterized documents.

Pixel output can differ between pdfium and Pillow versions. If the derived hash does not reproduce on another machine, verification fails; re-record it by deleting the raster file and running with `--pin --rasterize-ocr-layer`.

## Acceptance thresholds (asserted on the ingest files)

- scanned pages at least 20% of all pages
- table-heavy pages at least 30% (pdfplumber `find_tables`, lines strategy, a table of 2+ rows and 2+ columns)
- 5 to 8 revision pairs with both members present
- at most 1,500 pages in total
- no document with more than 50 scanned pages

The command prints the totals and exits 1 if any threshold fails.

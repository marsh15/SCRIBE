"""CSV parser (stdlib `csv`; named csv_rows to avoid shadowing the stdlib module).

Each data row is ONE `table` block:

    Table: <name> · Row <n> · <col>: <val> · <col>: <val>

* `row_number` is the 1-based number of the DATA row among non-blank records
  (the header, when present, is not counted). `meta["line"]` is the 1-based
  physical line where the record starts, and `meta["raw"]` is the original
  record text exactly as in the file (including quoted newlines).
* Empty cells are omitted from the row text. Decimal commas stay as written.
* `name` (keyword) is the file name shown in the row text; callers pass it via
  `parse(data, mime, name=...)`. Default "table".
* Encoding: utf-8 (a BOM is stripped) with latin-1 fallback.
* Delimiter: csv.Sniffer plus a consistency check, fallback order , ; TAB |.
* Header: first row is a header when all its cells are non-empty, non-numeric,
  label-like (no 3+ digit runs) and unique, and at least one data row follows
  (csv.Sniffer.has_header is not used: unreliable). Otherwise names column_1..column_n are generated
  (`generated_header=True`). Extra cells beyond the header get generated names.
* Rows beyond MAX_ROWS (5,000, DESIGN D21) are not indexed; the document gets
  `meta["truncated"]`, `meta["truncated_rows"]` and
  `warnings=["CSV_ROWS_TRUNCATED:<n>"]`.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter

from .errors import IngestError
from .model import Block, ParsedDocument, assemble

MAX_ROWS = 5000
CANDIDATE_DELIMITERS = [",", ";", "\t", "|"]
_NUMERIC = re.compile(r"^[+-]?[\d.,\s]*\d[\d.,\s]*%?$")
_DIGIT_RUN = re.compile(r"\d{3,}")


def _decode(data: bytes) -> tuple[str, str]:
    # NUL cannot be stored in Postgres text/jsonb: a hostile file would otherwise fail every retry with an INTERNAL error
    try:
        text = data.decode("utf-8-sig")
        return text.replace("\x00", ""), "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1").replace("\x00", ""), "latin-1"


def _records(text: str, delimiter: str, limit: int | None = None) -> list[list[str]]:
    out: list[list[str]] = []
    for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
        if any(c.strip() for c in row):
            out.append(row)
        if limit and len(out) >= limit:
            break
    return out


def _score(text: str, delimiter: str) -> tuple[float, int]:
    rows = _records(text, delimiter, 60)
    if not rows:
        return (0.0, 0)
    counts = Counter(len(r) for r in rows)
    modal, freq = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    if modal < 2:
        return (0.0, 0)
    return (freq / len(rows), modal)


def _sniff_delimiter(text: str) -> str:
    sample = text[:32768]
    scores = {d: _score(sample, d) for d in CANDIDATE_DELIMITERS}
    best = max(CANDIDATE_DELIMITERS, key=lambda d: scores[d])  # ties -> earlier in fallback order
    if scores[best] == (0.0, 0):
        return ","
    try:
        sniffed = csv.Sniffer().sniff(sample, delimiters="".join(CANDIDATE_DELIMITERS)).delimiter
        if scores.get(sniffed) == scores[best]:
            return sniffed
    except csv.Error:
        pass
    return best


def _is_label(cell: str) -> bool:
    c = cell.strip()
    return bool(c) and not _NUMERIC.match(c) and not _DIGIT_RUN.search(c) and len(c) <= 60


def _looks_like_header(first: list[str], following: list[list[str]], delimiter: str, text: str) -> bool:
    if not following:
        return False
    cells = [c.strip() for c in first]
    if not all(_is_label(c) for c in cells):
        return False
    if len({c.lower() for c in cells}) != len(cells):
        return False
    return True


def parse_csv(data: bytes, *, name: str = "table") -> ParsedDocument:
    text, encoding = _decode(data)
    if not text.strip():
        raise IngestError("EMPTY_EXTRACTION", "The file is empty.")
    delimiter = _sniff_delimiter(text)

    lines = list(io.StringIO(text, newline=""))
    pos = [0]

    def feed():
        for i, ln in enumerate(lines):
            pos[0] = i
            yield ln

    reader = csv.reader(feed(), delimiter=delimiter)
    records: list[tuple[list[str], int, str]] = []  # (cells, start line 1-based, raw)
    prev_end = -1
    try:
        for row in reader:
            start, end = prev_end + 1, pos[0]
            prev_end = end
            if not any(c.strip() for c in row):
                continue
            raw = "".join(lines[start : end + 1]).rstrip("\r\n")
            records.append((row, start + 1, raw))
    except csv.Error as exc:
        raise IngestError("INVALID_DOCUMENT", "This CSV file could not be read.") from exc
    if not records:
        raise IngestError("EMPTY_EXTRACTION", "No readable text was found in this file.")

    first = records[0][0]
    if _looks_like_header(first, records[1:], delimiter, text):
        header = [c.strip() for c in first]
        data_records = records[1:]
        generated = False
    else:
        header = []
        data_records = records
        generated = True

    width = max((len(r[0]) for r in data_records), default=0)
    width = max(width, len(header))
    if generated:
        header = [f"column_{i + 1}" for i in range(width)]
    expected = len(first) if not generated else width
    ragged_any = any(len(r[0]) != expected for r in data_records)
    names = header + [f"column_{i + 1}" for i in range(len(header), width)]

    blocks: list[Block] = []
    total = 0
    for cells, line, raw in data_records:
        total += 1
        if total > MAX_ROWS:
            continue
        ragged = len(cells) != expected
        parts = [f"Table: {name}", f"Row {total}"]
        for i, val in enumerate(cells):
            v = " ".join(val.split())
            if v:
                parts.append(f"{names[i]}: {v}")
        blocks.append(
            Block(
                type="table",
                text=" · ".join(parts),
                row_number=total,
                meta={
                    "raw": raw,
                    "line": line,
                    "cells": [c.strip() for c in cells] + [""] * (width - len(cells)),
                    "ragged": ragged,
                },
            )
        )
    if not blocks:
        raise IngestError("EMPTY_EXTRACTION", "No readable text was found in this file.")

    truncated_rows = max(0, total - MAX_ROWS)
    doc = assemble(blocks)
    doc.meta = {
        "encoding": encoding,
        "delimiter": delimiter,
        "header": names,
        "generated_header": generated,
        "ragged": ragged_any,
        "rows": min(total, MAX_ROWS),
        "truncated": truncated_rows > 0,
        "truncated_rows": truncated_rows,
    }
    if truncated_rows:
        doc.warnings.append(f"CSV_ROWS_TRUNCATED:{truncated_rows}")
    return doc

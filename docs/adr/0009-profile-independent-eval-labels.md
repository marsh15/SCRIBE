# ADR-0009: Evaluation labels are independent of any index profile

## Status

Accepted

## Context

Chunk IDs change with every parser or chunker.

## Decision

Gold evidence is `(doc, version, quote, page, grade)`. A retrieved chunk is relevant if its normalized text contains the normalized quote. Splits are fixed dev/validation/test; test is used only for milestone reports.

## Consequences

One label set scores legacy chunks, new chunks and future parsers.

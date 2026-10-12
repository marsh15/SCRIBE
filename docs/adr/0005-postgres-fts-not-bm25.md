# ADR-0005: Lexical search is Postgres FTS, not BM25

## Status

Accepted

## Context

Postgres `ts_rank` is not BM25. Calling it BM25 would misstate the system.

## Decision

Two generated tsvector columns: `fts_en` (stemmed, heading weight A, body B) and `fts_simple` (unstemmed, for identifiers). `pg_trgm` is added only if identifier queries measure poorly.

## Consequences

Documentation and UI say "Postgres full-text search". True BM25 is deferred (ADR-0012).

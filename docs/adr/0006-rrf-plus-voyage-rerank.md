# ADR-0006: Reciprocal rank fusion plus Voyage rerank

## Status

Accepted

## Context

Dense and lexical scores are on different scales and must not be added.

## Decision

Candidates are fused by RRF (k=60) using ranks only, then the top 20 are reranked by Voyage rerank-2.5 behind a replaceable adapter. If the reranker fails the fused order is used and the result is marked degraded.

## Consequences

Reranker top score drives the answerability gate. Rerank responses are cached for deterministic evals.

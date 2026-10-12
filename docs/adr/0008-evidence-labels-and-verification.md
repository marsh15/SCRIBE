# ADR-0008: Evidence labels and claim verification

## Status

Accepted

## Context

Prompted citations are unverifiable and the fallback Sources section was fabricated.

## Decision

Retrieval returns an evidence bundle with labels `[E1]…`. The model cites labels; after streaming, deterministic checks (label exists, version matches, quote hash matches) and one bounded semantic verifier call set each claim's status. The fabricated fallback is deleted. Supermemory is disabled in evidence mode.

## Consequences

"Verified" appears only when both checks pass. Timeouts and oversized answers show UNVERIFIED.

# ADR-0011: Source versions and index generations

## Status

Accepted

## Context

Re-ingestion deleted old chunks first, so a failed update left a document unsearchable.

## Decision

Each upload is an immutable source version. Each (version, index profile) builds an index generation. `active_indexes` points at one generation per source and profile and is switched atomically. Superseded generations keep chunks and spans but drop embeddings.

## Consequences

Old citations stay resolvable. Searching old versions is P1.


## Workspace

The isolation boundary for Sources, chats, traces and indexes. Each user has one personal workspace; a separate `eval` workspace holds benchmark corpora. Every data access is scoped to exactly one workspace.

## Source version

An immutable stored original for a Source (`source_versions`). A new upload creates the next version; stored bytes never change. A Source has a desired version and an active version.

## Index profile

An immutable combination of parser profile, chunk profile and embedding profile. Changing any part creates a new profile.

## Index generation

The chunks, spans and embeddings built from one Source version under one Index profile. Status: `BUILDING`, `READY`, `ACTIVE`, `SUPERSEDED`, `FAILED`, `CANCELLED`. At most one generation per Source and Index profile is active.

## Evidence bundle

The labelled passages (`E1`, `E2`, …) returned for one retrieval, with source version, quote and location. The model may cite only labels in the bundle.

## Answerability

The gate's verdict on a question: `ANSWERABLE`, `INSUFFICIENT_EVIDENCE`, or `RETRIEVAL_FAILED`. Based on the reranker's top score, never on cosine similarity.

## Degraded

A retrieval that completed with a fallback (for example `rerank_unavailable`). It is labelled in the response and the UI, never hidden.

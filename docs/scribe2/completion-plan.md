# Scribe 2.0 completion plan

Updated 2026-10-30. Scribe is an evidence-first workspace for private documents: find an answer, open its exact source passage and version, and see which claims the evidence supports. Keep the existing Next.js, Python, Postgres/pgvector, Clerk and Blob architecture.

The rough plan largely describes mechanisms already implemented. Completion means proving their behavior and closing specific gaps, rather than adding another retrieval engine or provider. Repository tests and a successful build do not establish production readiness.

## Implemented in this delivery

| Area | Change | Proof |
|---|---|---|
| Answer evidence | Each answer has an expandable source and claim-verification inspector, including restored messages. Opening a source reuses the access-checked citation viewer with the stored quote and version. | DOM tests cover separate answers reusing E1, streaming updates, unavailable verification and restored tool results; existing citation endpoint tests cover authorization. |
| Runtime-role bootstrap | Personal workspace resolution calls the existing `app_ensure_personal_workspace` database function, binding the authenticated server user ID and surfacing failures. | SQL-binding and error tests plus concurrent first resolution as non-owner `scribe_app` on local PostgreSQL. Migration 0013 is required. |
| Retrieval gate | Baseline creation requires reviewed cases. Comparison pins configuration, model IDs, threshold and calibration; incomplete/nonfinite metrics and missing isolation results fail. | Synthetic gate tests, including intentionally reversed fusion, cache misses and cross-workspace leaks. These are tests of the gate, not accuracy measurements. |
| Migration metadata | Offline journal validation runs before connecting. Registration rejects conflicting hashes/timestamps, duplicate entries and unexpected future entries atomically. | Temporary local database tests prove repeatability and rollback on conflicts. No production journal was inspected or changed. |
| Upload recovery | A retried completion callback checks the stored blob before retrying idempotent storage accounting and restarting queued ingestion. Duplicate, deleted and stale uploads are excluded. | Policy and route tests inject a failed usage write, replay the same callback and check idempotency, race handling and eligible tick behavior. |
| CI diagnostics | Retrieval status, result JSON when available and failure logs are retained for 14 days. | Workflow uses Bash pipe failure propagation, so preserving logs does not swallow gate failures. Remote CI still needs to run on the resulting commit. |
| Per-search diagnostics | Each answer keeps its own searches separate, showing the recorded query, stage ranks/timing, answerability and uncalibrated/degraded notices. Exact Python traces load on demand for stored cost, scores and source versions, with deleted/purged identities withheld. | UI and Next route tests; Python token matrix and workspace-scoped integration tests. No snapshot cutoff is invented. |
| Chat RLS context | Chat CRUD, message persistence, legacy search and trace operations execute with transaction-local workspace context. Chat deletion uses the existing FK cascade atomically. | A reproducible local test runner creates a real restricted login and isolated database, checks exported functions, foreign IDs, rollback and same-connection context cleanup, and removes its resources. The runner is included in CI. |
| Internal-token lifetime | Issuance and expiry share one timestamp so crossing a clock second does not extend the configured 60-second lifetime. | A boundary-time regression test and an in-memory relative-expiry mutation check distinguish the fixed behavior from the previous behavior. |

## Remaining milestones, in order

### 1. Rehearse and promote safely

Use [neon-rehearsal.md](neon-rehearsal.md). Record the actual deployed SHA, flags, role and migration journal. Verify a backup and its restore window; rehearse migrations 0008 onward on an isolated production copy, preserve ownership and source identities, compare schema fingerprints and backfill, and exercise the compatible rollback build. Give preview its own database before testing there. Test ingestion, retrieval, citations, updates and deletion on preview before any promotion.

**Completion evidence:** saved branch pre/postchecks, backup/restore rehearsal, preview smoke results, migration timings, deployment SHA, rollback target and an authenticated recording of the running system. Production state was not independently rechecked during this code delivery.

### 2. Finish database-enforced isolation

Workspace bootstrap and the chat lifecycle now work with a restricted role. The remaining file, ingestion and eval reads/writes need transaction-local context; global admin queries, runtime schema repair and queue/purge boundaries need explicit role treatment. Separate migration and runtime credentials only after these callers have been migrated. The local PostgreSQL bridge tests actual role enforcement and generated SQL, while live Neon HTTP behavior remains a deployment check.

**Completion evidence:** run every API and database cross-workspace test using real non-owner login credentials, verify pooled context resets, revoke access and retry guessed source/evidence IDs, then repeat on the isolated deployment. Existing application predicates remain necessary.

### 3. Arm a defensible retrieval baseline

Review validation labels and supporting passages through `scribe-eval review`, recording reviewer decisions. Keep the final test split untouched. Use the existing immutable dense, lexical `or_rank`, hybrid and rerank profiles with controlled ingestion/chunking/embedding inputs. Configure a real reranker before claiming its results.

Use [release-rules.md](release-rules.md) and [the gate procedure](../../benchmarks/gate/README.md) to record the real provider cache and write a reviewed baseline. Review baseline/cache changes as changes to the acceptance bar; repository code cannot substitute for reviewer approval or branch-protection settings. Demonstrate a deliberate retrieval regression is rejected in CI.

**Completion evidence:** reviewed label provenance, pinned dataset/profile/model metadata, reproducible runs and confidence intervals, a committed real baseline/cache and an armed CI run. Ranking quality, real latency and cost remain **NOT MEASURED by this delivery**.

### 4. Complete advanced evidence diagnostics

The per-answer inspector now covers sources, claim statuses and separate search diagnostics, including queries, candidate stage ranks/scores, answerability/reason/calibration, recorded timings and stored cost/source versions. Python retrieval traces are distinct from legacy `rag_traces`; the exact-trace endpoint never substitutes the chat's latest trace. Advanced details stay collapsed and authorize stored reads again. Explicit structured conflict explanations, verification latency and a retrieval snapshot cutoff are not currently persisted; do not infer them from a source timestamp or trace creation time.

**Completion evidence:** a user can follow a claim to its exact versioned passage, understand missing or contradictory evidence and inspect the correct answer's retrieval. Exercise keyboard navigation, mobile layout, loading, failure and deleted-source states in the authenticated browser.

### 5. Prove lifecycle failure recovery on the isolated deployment

Reuse the existing leases, version records and atomic index activation. Local tests cover callbacks, expired leases, provider failures, duplicate versions and active generation isolation. Repeat worker interruption, failed reindex, duplicate callbacks, source deletion and unauthorized retry/purge with the real storage and provider boundaries.

**Completion evidence:** a failed reindex leaves the prior valid index searchable until replacement activation; retries recover without duplicate active generations or usage; source deletion cleans stored data. Provider timeouts and failures must remain bounded and diagnosable.

### 6. Validate realistic use and record the demonstration

Observe consenting users retrieving and checking policy/procedure passages from non-sensitive authorized documents. Record task completion, time to evidence, interpretation of verification statuses and error recovery. Keep the existing animated walkthrough labelled as an illustration; add a separate authenticated recording from the running deployment.

Scribe is fully complete when these operational and user-facing outcomes have evidence. No multi-agent debate, graph RAG, vector database replacement, fine-tuning, autonomous browsing, marketplace or extra provider integration is needed.

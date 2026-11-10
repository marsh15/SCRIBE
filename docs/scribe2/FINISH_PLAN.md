# Finish plan

Maps every remaining item to files, tests and a verification command. Reconciled against the code on 2026-11-10 (main at `655e157` plus the branches below). Where an older document disagreed with the code, the code won and the document was corrected. Status words: **DONE** (code and tests, locally verified), **PREPARED** (written and checked offline, needs your keys or remote access to run), **BLOCKED** (waiting on something only you can provide; see [HUMAN_TODO.md](HUMAN_TODO.md)).

## Branch and PR map

One concern per branch (`scribe2/<task>`), each a PR into `main`, none merged by the assistant. Merge in this order; none adds a migration, so none is HOLD.

| # | Branch | Concern |
|---|---|---|
| 1 | `scribe2/rls-files-ingestion` | database-enforced isolation for files, ingestion, versions, queue and eval; the Python indexer fix found by running the service as a non-owner login |
| 2 | `scribe2/lexical-or-rank-default` | the app and the gate serve the OR-ranked hybrid profile; deterministic tie-breaking |
| 3 | `scribe2/lifecycle-recovery` | failure-recovery tests (provider timeouts, retry authorization) and the mapping of the rest |
| 4 | `scribe2/hermetic-corpus-fetch-tests` | the corpus-fetch tests no longer depend on free disk space |
| 5 | `scribe2/review-split-arm-gate` | `scribe-eval review --split`, `scripts/arm-gate.sh`, gate-test label, review checklist, `final.md` template |
| 6 | `scribe2/smoke-lifecycle` | `scripts/smoke.ts --lifecycle` (update and delete) and `DELETE /api/sources/[id]` |
| 7 | `scribe2/evidence-diagnostics` | milestone 4 gaps (see A3) |
| 8 | `scribe2/finish-docs` | runbook, human to-do list, user-study protocol, demo script, and the README / HANDOVER / completion-plan / summary-bullets corrections |

PR links are listed in the final report; each PR description repeats the commands that were run.

## A. Code

### A1. Database-enforced isolation (milestone 2): DONE locally, one deployment check remains

| Piece | Files | Test | Verify |
|---|---|---|---|
| Statement helper | `lib/scoped.ts` (`scoped(id).sql`, `scoped(id).batch`, `dbErrorText`) | `__tests__/scoped.test.ts` | `npx vitest run __tests__/scoped.test.ts` |
| Files, ingestion, versions, queue, delete, view | `lib/files/source-detail.ts`, `lib/ingestion/{source-intake,source-versions,source-delete,python-queue}.ts`, `app/files/actions.ts`, `app/files/[id]/actions.ts`, `app/api/files/[id]/view/route.ts` | unit tests updated to assert the workspace each statement ran in; **real non-owner login**: `__tests__/files-ingestion-rls-login.db.test.ts` (10 tests) | `intelligence/.venv/bin/python scripts/test-chat-rls.py $DATABASE_URL_TEST` |
| Global admin queries, runtime schema repair, queue and purge boundaries | owner connection `adminSql`/`adminDb` in `lib/db-config.ts` (env `NEON_ADMIN_DATABASE_URL`, falls back to `NEON_DATABASE_URL`, so nothing changes until you split credentials); allow-listed callers: admin analytics, admin migration route, `lib/db-compat.ts`, `lib/eval/queries.ts`, `requeueFailedBackfillJob`, the cron-only part of `runQueuedSourceIntake`, the backfill tool. Purge listing uses the existing `app_list_purgeable_sources` function; per-row deletes run in their own workspace. | `__tests__/security/db-boundary.test.ts` fails when any other module imports the owner connection or a raw unscoped `sql` | `npx vitest run __tests__/security/db-boundary.test.ts` |
| The Python service as a non-owner login | `scripts/test-python-rls.py` runs 8 endpoint suites (154 tests: IDOR, adversarial documents, log canary, evidence open, ingest, verify, retrieve, compare) with the app connected as a real `scribe_app` member. It found a real bug: `embed_generation` and the already-active branch of `build_generation` read without setting the workspace context, so under RLS the indexer saw "nothing to embed" and left every source `BUILDING`. Fixed in `intelligence/src/scribe_ingest/indexer.py`. | `tests/security/test_idor_python.py::test_app_connection_role_matches_the_configured_login` proves the login is not superuser/bypass | `intelligence/.venv/bin/python scripts/test-python-rls.py $DATABASE_URL_TEST` |
| Pooled context reset, revoked access, guessed IDs | in the two login suites: context is empty after every call on the same physical connection; `REVOKE SELECT ... FROM scribe_app` makes scoped reads fail; another workspace's source id yields 404 / nothing for view, detail tabs, delete, retry, upload completion, retrieve, evidence open, compare, ingest | as above | as above |

Mutation check: pointing one statement at the wrong workspace made 2 of the 10 login tests fail; reverted.

**Still a deployment check (HUMAN_TODO 12):** the whole Python service as `scribe_app` against Neon, Neon HTTP behaviour of `scoped()`, and the `FORCE ROW LEVEL SECURITY` step. Today the app connects as the owner, which bypasses RLS; application predicates remain the isolation in production until the credentials are split. The ingest worker, `scribe-eval ingest-corpus` and `scribe_ingest.upgrade` still need a privileged connection by design.

### A2. Lifecycle failure recovery (milestone 5): DONE locally

Existing tests already prove most items; two gaps were closed.

| Required behaviour | Evidence |
|---|---|
| Failed reindex leaves the previous valid index searchable until atomic activation | `test_indexer_lifecycle.py::test_failed_embed_keeps_previous_generation_active`, `test_source_versions.py::test_failed_new_version_leaves_the_previous_one_searchable`, `::test_killed_mid_embed_keeps_v1_searchable_then_resume_activates_v2` |
| Duplicate callbacks and retries: no duplicate active generations, no double-counted usage | `test_source_versions.py::test_duplicate_callback_is_a_noop_even_when_concurrent`, `test_indexer_lifecycle.py::test_rerun_is_idempotent` and `::test_corpus_rerun_creates_no_duplicates`, `test_ingest_steps.py::test_step_killed_mid_embed_resumes_without_duplicates` (one usage event per batch, none duplicated), `test_ocr_steps.py::test_usage_events_are_idempotent_per_page`, `__tests__/upload-accounting.test.ts` |
| Expired leases recover | `test_ingest_steps.py::test_stale_lease_writes_zero_rows`, `::test_step_killed_mid_embed_resumes_without_duplicates` (sweep), `test_rls.py::test_claim_reclaims_expired_lease_and_sweep_releases_it` |
| Source deletion purges stored data and blobs | `test_source_delete_purge.py::test_purge_removes_every_row_and_every_version_blob`, `::test_purge_failure_is_recorded_retried_with_backoff_and_visible`, `test_legacy_hard_delete.py` |
| Provider timeouts bounded and diagnosable | **new** `tests/integration/test_lifecycle_recovery.py`: bounded attempts, error class `TRANSIENT_PROVIDER` with `ReadTimeout` in the message, no document text, and a step-level check that the job becomes `retrying` with a visible error code and nothing is activated. Rerank: `test_rerank.py::test_timeout_is_transient_and_degrades_candidates`. Verification: 20 s timeout to UNVERIFIED in `test_verify.py`. OCR: `test_ocr_steps.py::test_transient_provider_error_keeps_finished_pages_and_retries` |
| Unauthorized retry or purge rejected | purge endpoint scope: `test_source_delete_purge.py::test_purge_endpoint_requires_scope_and_purges`; token matrix `test_token_matrix.py`; retry of a foreign Source: `files-ingestion-rls-login` ("queue helpers cannot touch another workspace's job"); **new** `__tests__/source-retry-authz.test.ts` (unauthenticated, not-failed and foreign cases never requeue or kick the chain); the admin retry route is admin-gated in `admin-migration-route.test.ts` |

Not provable locally (needs real storage and providers, see HUMAN_TODO 9-10): worker interruption on Vercel, real Blob deletion, real provider timeouts.

### A3. Advanced evidence diagnostics (milestone 4): DONE in code and DOM tests; browser check is yours

Branch `scribe2/evidence-diagnostics` (`components/answer-evidence-inspector.tsx`, `__tests__/evidence-inspector-claims.test.tsx`).

| Gap in the plan | Change | Test |
|---|---|---|
| "Follow a claim to its exact versioned passage" | each cited label in a claim row is a keyboard-operable citation link (the existing access-checked viewer, which shows the version and the stored quote); a label without stored evidence reads "no stored evidence" and is not a link | claims lead to the exact passage |
| Conflict explanation, verification latency, retrieval snapshot cutoff are not persisted | listed under "Not recorded" with the reason; never inferred from a timestamp | the inspector never invents data |
| Loading, failure, deleted-source states | covered with DOM tests of the viewer: loading text, a retryable failure (502 and network error), not found, deleted source with the quote withheld | citation viewer states |
| Keyboard | open with Enter/Space on the link, focus moves into the dialog, Escape closes it; the inspector itself is a native `<details>` | claims lead to the exact passage |

**Not verified:** mobile layout and the authenticated flow in a real browser (needs a Clerk session, which this environment does not have). The viewer already uses 44 px targets and `max-h-[90vh] overflow-y-auto`, but that is a reading of the classes, not a check. Do it while recording the demo (HUMAN_TODO 14).

### A4. Lexical retrieval (task 4): DONE structurally, quality NOT MEASURED

`or_rank` (OR-style matching with `ts_rank_cd` and an identifier boost) already existed as the `*-v2` profiles, but nothing served them: the app and the gate used `hybrid-rrf-rerank`, whose lexical list is empty for most natural-language questions, so "hybrid" was dense search.

| Change | Files | Test (written first, failed first) |
|---|---|---|
| The app serves `hybrid-rrf-rerank-v2`; the gate guards the same profile | `lib/python-retrieval.ts` (`EVIDENCE_PROFILE`), `intelligence/src/scribe_eval/gate.py` (`DEFAULT_PROFILE`) | `tests/retrieval/test_lexical_default_wiring.py`: gate default is the `or_rank` hybrid; the TS constant equals the gate default; older profiles unchanged; on the first 20 **dev** questions the `and` hybrid had lexical candidates for at most 10, the `or_rank` hybrid for at least 18 (fake embeddings, fixture corpus: a structural statement, not a quality measurement) |
| Candidate order no longer depends on random chunk UUIDs. Found while regenerating the gate fixtures: independent ingests of the same corpus gave different orders in 19 of 40 question/profile pairs (ties broken by `chunk_id`), which would make the cached reranker inputs of an armed gate miss on unchanged code. Ties now break on (document name, content hash), then id. | `retrieval/fusion.py`, `retrieval/lexical.py` (or_rank SQL only; the `and` SQL stays byte-pinned), `retrieval/dense.py` | `test_candidate_order_does_not_depend_on_chunk_uuids`; `test_rrf.py::test_tie_break_lists_then_best_rank_then_content` |
| Seeded gate fixtures follow the default profile; committed seeded baseline regenerated (still stamped `SEEDED FIXTURE - NOT A MEASUREMENT`) | `tests/evaluation/_gate_seed.py`, `gate_seeded/baseline.seeded.json` | `tests/evaluation/test_gate.py` (reproducible from a fresh seeded cache) |

The dev split was used only to choose example questions; nothing was tuned; the test split was not touched (INV-12). Whether `or_rank` ranks better is **NOT MEASURED**: it needs the reviewed golden set and your keys (HUMAN_TODO 4, which also says how to switch the default back).

### A5. Re-audit: DONE

| Pass | Result |
|---|---|
| Security review of the whole diff (auth, RLS context, owner connection, the new `DELETE` route, scripts, SQL parameterisation) | no findings above the bar; two informational notes: until `NEON_ADMIN_DATABASE_URL` is set the owner connection is the same URL as the runtime one, and `getFileWithChunks` filters chunks by file id only after an ownership-checked lookup (RLS covers it once the role is restricted) |
| Correctness review of the diff | no result-shape, type-parsing, parameter or error-wrapping regressions in the raw-SQL conversion. Real issues found and fixed, each with a test: `arm-gate.sh` used BSD-only `stat -f` first (would fail on the Linux CI runner), accepted `--help` as a paid run, counted `KEY=""` as a key and ignored `ARM_GATE_DATASET` for the paid steps; the backfill tool could write to the app's owner URL instead of the URL it announced; ORDER BY used the database's default collation for the document name (now `COLLATE "C"`); the smoke script treated any 409 as an engine skip and did not prove a citation opened before the delete |
| Migration review | no migration was added or edited (`git diff --stat drizzle/` is empty). Existing migrations: applied twice on a 1.0-shaped database, as a non-superuser (`scripts/test-migrations-nosuperuser.sh`), and fingerprint-diffed (see C) |

### A6. Environment finding

Two Python tests (`test_corpus_fetch`) passed or failed depending on free disk, because the tool refuses to run under 2 GiB free. They are now hermetic and the guard has its own test. The machine's data volume repeatedly hit 100%, which kills Docker; about 9 GB is held by stale worktrees of already merged branches (yours to remove).

## B. Evaluation and gate

| Item | Status | Where |
|---|---|---|
| Efficient review pass | DONE: `scribe-eval review --split validation,dev,test` (the splits you name first, concern cases first inside each) | `intelligence/src/scribe_eval/review/store.py::split_first_order`, `commands/review.py`, tests in `tests/evaluation/test_review_tool.py` |
| One-page checklist | DONE | [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md) |
| Arm the gate in one command | PREPARED, BLOCKED on keys and the validation review | `scripts/arm-gate.sh` (`--check` runs only the preflight; tests in `intelligence/tests/deployment/test_arm_gate_script.py`) |
| `final.md` template | DONE (NOT MEASURED everywhere; commands use `run-profile`, `score`, `report`) | [benchmarks/final.md](benchmarks/final.md) |
| A worsened retrieval is rejected by the gate in CI | DONE: `tests/evaluation/test_gate.py::test_hybrid_rrf_unchanged_passes_and_reversed_rrf_fails_the_gate` (labelled a gate test, not an accuracy measurement; runs in the CI python job) | |

## C. Deployment readiness (nothing executed against remote infrastructure)

| Item | Status | Where |
|---|---|---|
| Rehearsal phases consistent with migrations 0001-0020 and runnable locally | DONE for phases 0 and 2-7 on a local throwaway database built from the 1.0 schema (`0edddb1`): `check-journal.mjs` listed 13 files; precheck ran; `apply.sh` applied all 13 files twice (1 s each, idempotent); `postcheck.sql` sections ran and the schema fingerprint differs from a fresh 2.0 schema in exactly the three known lines; `--register-journal` twice left 13 rows, last `created_at` 1784937600000 (= 0020); the TS backfill (psql path) created versions and legacy indexes and its re-run saw 0 files; `scribe_ingest.upgrade backfill --dry-run` ran; the `scribe_app` login saw 0 rows without context and one workspace with it; after migration a 1.0-shaped `chats` and `files` insert landed in a workspace (0020). **Not run (need Neon or a deployment):** phase 1 (branch creation), phase 8 (app against the branch), phase 9 (the 1.0.1 build), phase 10 (Neon restore). The local run used a superuser; the non-superuser path is covered by `scripts/test-migrations-nosuperuser.sh`. | `docs/scribe2/neon-rehearsal.md`, `scripts/neon-rehearsal/*` |
| Preview smoke (ingest, retrieve, cite, update, delete) | DONE as a script; not run against any deployment | `scripts/smoke.ts --lifecycle`, [T02-verification.md](T02-verification.md) |
| Promotion and rollback runbook with stop-and-verify points | DONE | [runbook-promote-rollback.md](runbook-promote-rollback.md) |
| Preview has its own database | documented loudly (README, runbook, neon-rehearsal); the change itself is yours | HUMAN_TODO 8 |

## D. Documentation

| Item | Status |
|---|---|
| README, HANDOVER, completion-plan, summary-bullets accurate | DONE on `scribe2/finish-docs`; every number there is either re-run on 2026-11-10 (date and command stated) or marked "NOT MEASURED" |
| User-study protocol | DONE: [user-study-protocol.md](user-study-protocol.md) |
| Demo script for an authenticated recording | DONE: [demo-script.md](demo-script.md); the animated walkthrough stays labelled as an illustration |
| HUMAN_TODO | DONE: [HUMAN_TODO.md](HUMAN_TODO.md) |

## Local gate (what was run, on what)

Run on 2026-11-10 on the combined working tree (all branches below together; `/tmp/finish-logs/gate.sh`), after the review fixes:

| Check | Result |
|---|---|
| `uv run pytest` (intelligence), run twice | 2282 passed, 1 skipped, 1 xfailed, both runs (about 5 min each) |
| `npx tsc --noEmit`, `eslint` | clean (`brag-output/`, a gitignored local folder, excluded) |
| `npm test` (vitest) | 508 passed, 17 skipped (need a database) |
| `scripts/apply-migrations.sh` on a throwaway database | exit 0 |
| `scripts/test-migrations-nosuperuser.sh` | exit 0 |
| `scripts/test-chat-rls.py` (real non-owner login, TypeScript code) | 14 passed |
| `scripts/test-python-rls.py` (real non-owner login, Python endpoints) | 154 passed |
| `__tests__/scoped.db.test.ts` with `DATABASE_URL_TEST` | 4 passed |
| `scripts/check-py-bundle.sh` | exit 0 |
| `npm run build` (placeholder Clerk keys) | exit 0 |

Each branch was then checked alone in a clean worktree (TypeScript: `tsc`, lint, full vitest; Python: the suites it touches); see each PR description. GitHub CI runs the full gate per PR. A first run of the same gate failed in 9 `test_corpus_fetch` tests (free disk below 2 GiB) and 3 vitest tests (stale profile name, unregistered new route); both causes were fixed and the gate passed afterwards.

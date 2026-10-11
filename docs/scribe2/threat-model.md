# Scribe 2.0 threat model (T27)

Scope: the Next.js app, the Python intelligence service (`/api/py/*`), Postgres, Vercel Blob, and the model providers as
they touch user data. Status: written after the T27 adversarial suite; every mitigation below names the test that holds
it in place. This is an engineering review with tests, **not** an external penetration test (see section 7).

## 1. Assets

| Asset | Where | Why it matters |
|---|---|---|
| A user's documents: original bytes, blocks, chunks, embeddings | Vercel Blob (private), `source_versions`, `document_blocks`, `chunks`, `chunk_embeddings_*` | The product's whole value; private by definition |
| Quotes and answers derived from documents | `evidence_items.quote`, `chat_messages.parts`, `retrieval_traces.query`, `claim_verifications` | Same sensitivity as the documents |
| Workspace boundary | `workspaces`, `workspace_id` on every row, RLS policies (INV-1) | One user must never read or change another's data |
| Citation integrity | `[E#]` labels, evidence bundles, claim statuses (INV-9) | A "Verified" badge is a trust claim the UI makes |
| Secrets | `INTERNAL_SERVICE_SECRET`, `CRON_SECRET`, `BLOB_READ_WRITE_TOKEN`, Gemini / Voyage keys, Clerk keys | Mint tokens, read all blobs, spend money |
| Availability and cost | Hobby CPU hours, Gemini / Voyage quotas | A loop or flood costs real money and locks out the owner |

## 2. Trust boundaries

```
 browser ──(Clerk session)──▶ Next.js routes / server actions ──(60 s HS256 token)──▶ Python /api/py/*
                                   │                                                      │
                                   └────────────── Postgres (RLS, scribe_app) ◀───────────┘
 document bytes ─▶ parsers ─▶ chunks ─▶ evidence text ─▶ model prompt ─▶ answer ─▶ verifier ─▶ UI
      (untrusted)                                         (untrusted data inside markers)
 Vercel cron / own tick ──(CRON_SECRET bearer)──▶ /api/internal/ingest/*
 Vercel Blob / Razorpay ──(signed callbacks)──▶ upload-complete / webhooks
```

1. **Browser to Next.js.** Clerk session, resolved to `(userId, workspaceId)` server side (`lib/workspace.ts`). Nothing the browser sends selects a tenant.
2. **Next.js to Python.** An internal token minted per call: `sub`, `wid`, `scope`, `aud=scribe-intel`, 60 s, HS256. The workspace comes from the token only (INV-15).
3. **Python and Next.js to Postgres.** Workspace context via `set_config('app.workspace_id', …, true)` inside each transaction; RLS policies exist (migration 0013). See residual risk R1.
4. **Document content to the model.** The most dangerous boundary: any user, or any document a user uploads (including documents shared by email from outsiders), can contain text written by an attacker.
5. **Cron / self-fired ticks to internal routes.** Static bearer secret.
6. **Third-party callbacks.** Vercel Blob upload-complete (signature verified by `handleUpload`), Razorpay (HMAC).

## 3. Attackers

| # | Attacker | Capabilities | Goal |
|---|---|---|---|
| A1 | Another signed-in user | Own workspace, can guess or obtain ids (file, chat, message, evidence, bundle) | Read, change or delete another workspace's data |
| A2 | Document author | Anything inside an uploaded or shared file, including its file name | Make the assistant act on the document instead of the user: leak other data, change tools, fake citations, flip "Verified" |
| A3 | Anonymous internet client | HTTP only | Call internal / cron / Python / webhook endpoints, forge Blob or Razorpay callbacks |
| A4 | Curious operator / log reader | Read access to logs | Recover document text from logs |
| A5 | Compromised provider response | A model or API that returns hostile or malformed output | Break parsing, forge statuses |

Not modelled: a malicious operator with database owner access, a compromised Vercel account, a supply-chain compromise of a dependency.

## 4. Mitigations, invariants and tests

Python tests live in `intelligence/tests/security/` (shared test DB, fake embedder / reranker / verifier, no network).
TypeScript tests live in `__tests__/security/`. Fixtures: `fixtures/adversarial/` (manifest + six hostile documents + a foreign-workspace secret).

| Threat | Control | INV | Tests |
|---|---|---|---|
| A1 reads another workspace via retrieval | Workspace filter inside every SQL before `LIMIT`; workspace from token | 1, 2, 3, 15 | `test_adversarial_documents.py::test_hostile_queries_and_a_foreign_workspace_in_the_body_never_cross_the_tenant_boundary`, `test_idor_python.py::test_retrieve_source_ids_of_another_workspace_return_nothing`, `test_a_token_for_workspace_b_does_not_see_workspace_a` (existing: `test_rls.py`, `tests/retrieval/test_scoping.py`) |
| A1 guesses an evidence / bundle / message / source id | Reads go through workspace-scoped readers; a foreign id answers exactly like an unknown id (same status, same body) | 8, 15 | `test_idor_python.py` (`evidence_open`, `verify_foreign_bundle_and_message`, `verify_read`, `ingest_source_foreign_source_id`); TS `idor-sweep.test.ts` rows for `evidence/[id]`, `files/[id]/view`, `sources/[id]/versions`, `sources/process-now`, `rag/latest`, `chat`, and every id-taking server action |
| A1 abuses the SQL functions behind routes | `scribe_soft_delete_source` / `scribe_reserve_source_version` match `(workspace, user, source)` | 1 | `test_idor_python.py::test_soft_delete_function_refuses_a_foreign_workspace_or_user`, `test_reserve_version_function_refuses_a_foreign_workspace_or_user` |
| New route or action ships without an access decision | Guard tests read the file system and `proxy.ts` and fail on anything not in `route-table.ts` | – | `route-guard.test.ts` (routes, server actions, public paths), Python `test_token_matrix.py::test_route_table_matches_the_app_exactly` (endpoints and their scopes) |
| A3 calls Python directly | Missing / malformed / expired / wrong-audience / wrong-signature / `alg=none` / other-HMAC / claim-less / wrong-scope / scope-substring tokens are rejected on every endpoint, both URL prefixes | 15 | `test_token_matrix.py` (about 550 parametrised cases) |
| Body claims a workspace | No request model declares a workspace field; bodies carrying `workspace_id`, `wid`, `workspaceId`, `workspace`, `tenant_id` are ignored | 15 | `test_token_matrix.py::test_no_request_model_declares_a_workspace_field`, `test_idor_python.py` (`with_ws` bodies on retrieve, evidence, verify, verify/read, ingest/source), TS `process-now` and `rag/latest` rows |
| A3 calls cron / tick / sweep | `CRON_SECRET` bearer with constant-time compare; no secret configured fails closed; the routes are public in the proxy only because they check it | – | `idor-sweep.test.ts` (internal rows: seven bad-header variants plus unset secret), `proxy-access.test.ts`, existing `cron-auth.test.ts`, `ingest-tick.test.ts` |
| A3 forges Blob or Razorpay callbacks | `handleUpload` signature check and no client-token issuing at that URL; Razorpay HMAC | – | `idor-sweep.test.ts` rows `sources/upload-complete`, `webhooks/razorpay` |
| A2 "ignore previous instructions", fake system prompts, tool-call JSON, orders to reveal other workspaces | Document text is only ever placed between `BEGIN/END_EVIDENCE_DATA` markers, the system prompt says it is data, the model has one tool whose scope is fixed server side, the loop is capped at 3 steps | 13 | `adversarial-injection.test.tsx` (context for all six fixtures; one retrieve call, session workspace, fixed body; one-tool guard), `test_adversarial_documents.py` |
| A2 closes the data block / forges a label / forges the gate line | `<<<` and `>>>` runs and `[E#]` are defused in text, file names and headings; labels are issued by Python in order; the gate is computed from the reranker score | 13, 9 | `adversarial-injection.test.tsx::evidence context for …`, `test_adversarial_documents.py::test_labels_are_issued_by_the_server_in_order_whatever_the_document_says`, `test_forged_gate_text_cannot_raise_the_relevance_score` |
| A2 makes the model cite a label that does not exist | Every cited label is checked against the stored bundle of this turn; unknown labels render as invalid and are `INVALID_CITATION`, never sent to the verifier | 9 | `test_injected_quotes_cannot_flip_a_claim_status`, `adversarial-injection.test.tsx::a forged citation never becomes a valid one` |
| A2 flips a claim to VERIFIED | `VERIFIED` is produced in one function from deterministic checks AND a verdict enum; quotes and claims are defused and flattened in the verifier prompt; statuses shown by the UI come only from the service | 9, 13 | `test_injected_quotes_cannot_flip_a_claim_status`, `test_the_verifier_prompt_keeps_every_quote_inside_its_own_data_markers`, `test_claim_text_cannot_forge_extra_claims_or_markers_in_the_prompt`, TS `claim statuses come from the service only`, existing `test_verify.py` |
| A2 hides NUL bytes or corrupt files to wedge ingestion | Parsers strip NUL; corrupt files become permanent `INVALID_DOCUMENT`, not retried | – | `test_hostile_documents_parse.py`, `test_logs_canary.py::test_corrupt_documents_fail_without_leaking_text_to_logs_or_job_rows` |
| A4 reads logs | Only error classes, codes, counts and ids are logged; no query, quote, claim or exception message that can echo input | 13 | `test_logs_canary.py` (retrieve ok / embedding failure / rerank degrade, ingest step, `/ingest/source`, corrupt documents, provider errors that echo the text, evidence open, verify ok / error / timeout, purge), `log-capture.test.ts` |
| A5 sends a hostile verdict or status | Verdicts parsed against an enum and the indexes asked about; unknown statuses become `UNVERIFIED` in the UI parser | 9 | `test_verify.py`, TS `an unknown or injected status string is never shown as a status` |

## 5. Bugs found and fixed by T27

1. **Python service unreachable through Clerk.** `proxy.ts` did not list `/api/py/*`, `/api/internal/ingest/sweep` or `/api/internal/ingest/tick` as public, so Clerk would answer them with 404 for the server-to-server calls (retrieval, ingestion ticks and the daily sweep). They are now public and authenticate themselves. Not yet confirmed on a real Vercel deployment.
2. **Forged claim lines in the verifier prompt.** Claim text (model output shaped by documents) was inserted unescaped, so a newline plus `CLAIM 7 (evidence: E1): …` created a second claim line and markers could be planted. Claim text is now flattened to one line and defused.
3. **NUL bytes in CSV (and PDF / DOCX text) failed with an INTERNAL error and were retried.** NUL is now stripped at parse time.
4. **`/api/chat` and `/api/files/[id]/view` answered 500 when signed out** (the proxy hides this in production). Now 401.
5. **`/api/evidence/[id]` logged a fragment of a malformed Python response** (V8 quotes part of the body in its JSON error). The parse failure is now caught and answers 502 without logging the message.

## 6. Residual risks (accepted or open)

| # | Risk | Notes / next step |
|---|---|---|
| R1 | **RLS is not FORCEd.** The table owner bypasses policies. `drizzle/deferred/force_rls.sql` is guarded and not journaled. Until the app and Python connect as `scribe_app` (NOBYPASSRLS), tenant isolation rests on the application filters plus `set_config`, which the tests exercise but the database does not enforce for the owner role | Roll out per HANDOVER section 8, then run the deferred script |
| R2 | **No TypeScript caller uses `scoped()`.** All TS queries filter by hand (`workspace_id` and `user_id`). A forgotten filter would not be caught by the database | The sweep asserts the filters for today's routes; new queries need the same review |
| R3 | **Internal token secret.** One static HS256 secret shared by Next.js and Python, no key id, no rotation procedure. Rotation needs both sides redeployed together (old tokens live 60 s). Anyone with the secret can mint a token for any workspace | Add `kid` and two-key acceptance before real users; keep the secret out of preview builds shared with others |
| R4 | **`/api/py/*` is reachable from the internet.** The only gate is the token. No rate limiting, so a leaked token holder or a flood of invalid tokens costs CPU hours | Vercel firewall rule or a cheap pre-check; Hobby has no WAF |
| R5 | **Prompt injection can still influence prose.** A hostile document can steer what the assistant writes (tone, omissions, a look-alike markdown link such as `[E1](https://evil)` which renders as an ordinary external link). It cannot change tools, scope, label validity or claim status | Inherent to LLM answering; the verifier and badges are the mitigation, and they only judge cited claims |
| R6 | **The verifier is also an LLM.** An injected quote can make the verifier answer wrongly about the claim that cites it (SUPPORTED instead of UNSUPPORTED). It cannot raise a claim that failed the deterministic checks, and uncited or forged-label claims never reach it | Measure false-VERIFIED rate in T28 |
| R7 | **Answerability gate is uncalibrated** (tau 0.0 until T17), so low-relevance evidence is still ANSWERABLE | Planned |
| R8 | **Chat history keeps retrieved text.** `chat_messages.parts` stores the tool result (chunk contents) of each answer. After a Source is deleted or purged, evidence opening withholds the quote (INV-14) but the stored chat history still contains the text and the inspector can show it | Decide: strip tool results at purge time, or document the retention |
| R9 | **The legacy engine (`RETRIEVAL_ENGINE=legacy`, still the default) has none of the evidence controls.** It passes chunk text with the old prompt, and Supermemory can inject memory. The injection suite targets the Python engine | Flip the default after T17, then remove the legacy path |
| R10 | **TS error logging is raw in places.** `internalError`, `console.error("Search error:", error)` and the chat `onFinish` log whole error objects. Database or provider messages can echo SQL fragments or input | Log `error.name` plus an id everywhere; tested only for retrieval, evidence and verification paths |
| R11 | **Blob download trusts the stored URL.** `download_private_blob` sends the blob token to `files.storage_url` / `storage_key` (set from the signed Blob callback). There is no host allowlist, so an attacker who could write those columns could exfiltrate the token | Add a `*.blob.vercel-storage.com` allowlist in the downloader |
| R12 | **Forged upload-complete callbacks answer 500**, not 4xx. They are refused and complete nothing | Cosmetic |
| R13 | **Indistinguishable-empty instead of 404.** `rag/latest` returns `{trace: null}`, `process-now` returns its normal success body, and read actions return `[]` / `null` for a foreign id. This leaks no existence, but a client cannot tell "not yours" from "none yet" | By design; the sweep pins the behaviour |
| R14 | **Zero-width / look-alike characters.** Defusing matches the exact ASCII marker (`<<<`, `>>>`, `[E<digits>]`). Visually similar Unicode is not rewritten, so a model could be nudged by it (it still cannot close the real markers or mint a label) | Accepted |
| R15 | **Eval-run and compare endpoints do not exist yet** (T29/T30). The guard tests will fail until they are added to the tables | By construction |

## 7. Not covered

- **No external penetration test**, no fuzzing campaign, no formal review of the Vercel, Clerk, Neon, Blob or Razorpay configuration.
- Hostile **file formats against the parsers' own vulnerabilities** (pdfplumber / pdfium, python-docx, decompression bombs): only size, page and row limits and NUL handling are tested, not memory or CPU exhaustion.
- **Denial of service and cost abuse** beyond the documented limits (rate limits, per-user quotas under attack).
- **CSRF and session fixation** (left to Next.js server-action origin checks and Clerk), CORS, security headers, cookie flags.
- **Dependency and image vulnerabilities**, secrets in CI, supply chain.
- **Real providers**: Gemini and Voyage were never called in these tests (fake embedder, fake reranker, `FakeVerifier`); behaviour of a real model under injection is not measured. The injection tests prove what the *code* does with hostile text, not that a given model obeys the "data, not instructions" rule.
- **A real Vercel deployment**: the proxy fix for `/api/py/*` and the tick routes is covered by a unit test of `proxy.ts`, not by a deployed preview.
- Multi-user workspaces, roles and sharing (not in P0), billing fraud, Razorpay replay beyond the existing dedupe.

## 8. How to keep this true

- Add every new API route and server action to `__tests__/security/route-table.ts` (and a handler to `idor-sweep.test.ts` if it takes an id) or CI fails.
- Add every new Python endpoint to `ROUTES` in `intelligence/tests/security/test_token_matrix.py`.
- New logging statements must carry ids, codes and counts only; extend `test_logs_canary.py` / `log-capture.test.ts` for the new path.
- New document-derived text that reaches a prompt must pass through `neutralizeEvidenceText` (TS) or `verification.neutralize` (Python) and sit inside data markers.

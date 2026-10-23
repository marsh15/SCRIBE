# T02 verification checklist (Vercel preview)

Local and CI checks pass, but co-deployment on Vercel is NOT yet verified. Run this checklist on the first preview of the T02 branch.

## Env vars (Preview + Production)

- `INTERNAL_SERVICE_SECRET`: 32+ random bytes. Next.js and Python share one value (same project, so one variable).
- `NEON_DATABASE_URL`: pooled URL (the Python service falls back to it when `DATABASE_URL` is unset; it uses `prepare_threshold=None`).
- `VERCEL_AUTOMATION_BYPASS_SECRET`: enable "Protection Bypass for Automation" in Project Settings > Deployment Protection; Vercel exposes the secret under this name automatically.

## Checks

1. Build log shows both the Next.js build and a Python function for `api/py/index.py` (Functions tab lists it). If Vercel does not detect Python alongside Next.js, or install of `requirements.txt` fails, the fallback in ADR-0002 applies: a second Vercel project rooted at `intelligence/`.
2. Function size is well under the 500 MB limit and `intelligence/tests` is not in the bundle (inspect the function's files in the build output; `excludeFiles` in `vercel.json`, `.vercelignore`).
3. Unauthenticated, no bypass header: expect 401 from Vercel's protection page (HTML), which shows protection is on.
4. With bypass but no token:
   `curl -i -H "x-vercel-protection-bypass: $VERCEL_AUTOMATION_BYPASS_SECRET" https://<preview>/api/py/health` expect `401 {"detail":"unauthorized"}`. This is JSON from FastAPI, so routing and the rewrite reach Python and the unauthenticated auth guard responds. It does not verify the authenticated health handler or its database checks.
5. With a minted token (scope `health`, 60 s; mint via a tiny script using `lib/internal-token.ts` or `jwt.encode` with `aud=scribe-intel`):
   `curl -H "x-vercel-protection-bypass: ..." -H "Authorization: Bearer $TOK" https://<preview>/api/py/health` expect 200 with `ok:true`, a `postgres_version`, and `pgvector_version` non-null on Neon.
6. Path handling: the app serves both `/health` and `/api/py/health`, so it works whether or not the rewrite preserves the `/api/py` prefix. Record which path FastAPI actually saw (add a temporary log), and drop the unused route if you like.
7. Expired token (wait 61 s) and wrong audience return 401.
8. Repeat step 5 five times in a row to confirm no prepared-statement errors through the Neon pooler (`prepared statement ... already exists`).
9. Record the cold start latency of `/api/py/health`, and confirm the existing cron entry (`/api/internal/ingest/run`) is still listed under Settings > Cron Jobs.
10. From Next.js, `callIntelligence("/health", {sub, wid, scope: "health"})` (lib/intelligence-client.ts) works from a server route on the preview, using the bypass header. `VERCEL_URL` is the deployment-specific host, which is protected, so the call needs the header.

## Known unknowns

- `functions.maxDuration: 300` for a Python function on Hobby, and `includeFiles`/`excludeFiles` globs, are unverified against the current Vercel schema; the first deploy will reject invalid config.
- Whether root `requirements.txt` is honored when a root `package.json` exists.

## C4 local smoke and retrieval probe

`npx tsx scripts/smoke.ts --base-url http://localhost:3000 --session-token "$SCRIBE_SESSION_TOKEN"` checks public Next health and the unauthenticated Python health auth guard (expected 401), then reserves and uploads a small private text fixture, kicks `/api/sources/process-now`, and polls readiness by asking through the chat UI-message SSE endpoint. It retries up to four times with a one-second wait between attempts. An answer is ready only when it states the fixture's 17 days and cites an evidence item belonging to the uploaded source; the script then opens that citation and verifies the source and quote. The session token may instead be provided at runtime in `SCRIBE_SESSION_TOKEN`; it is only used as the Next.js `__session` cookie and is not printed or written to disk. The Blob upload follows the existing client upload flow with its one-use Blob client token and does not receive the Clerk cookie. Citation opening uses a same-origin `/api/evidence/<id>` URL and never forwards the Clerk cookie to a citation URL from the answer.

The Python `/api/py/health` unauthenticated check is intentionally an auth-boundary/reachability check. Its expected 401 does not prove the authenticated health route's database connectivity.

`scribe-perf probe --base-url http://localhost:3000 --count 20 --question "What does the policy say?" --token "$SCRIBE_PERF_TOKEN"` (or runtime `SCRIBE_PERF_TOKEN`) calls `/api/py/retrieve` with an opaque bearer token, then reports nearest-rank p50/p95 request latency and the mean `cost.usd` returned by retrieval. Multiple `--question` values are cycled across requests. Costs flagged `estimated: true` are labeled estimated; absent cost data is printed as `NOT MEASURED`, never as zero. Tokens are not written or printed. Use only a local stub for the perf probe.

Execution status for this handoff: **NOT RUN** against Next.js/Python services. This workspace has no authorized local Blob/provider credentials and authenticated session for the end-to-end upload and answer flow, and no deployed URL may be used. The smoke and probe orchestration is covered by mocked-fetch/local-stub tests; those do not claim an end-to-end service pass.

A real local upload also requires the existing Blob completion webhook to reach the same local Next.js instance; `process-now` cannot queue an upload whose completion callback has not arrived. A plain localhost callback is not reachable by Blob. Do not point `NEXT_PUBLIC_APP_URL` at a deployed service as a workaround. This integration requirement is not exercised by the injected upload tests.

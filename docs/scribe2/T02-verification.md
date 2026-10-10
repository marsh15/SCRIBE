# T02 verification checklist (Vercel preview)

Local and CI checks pass; co-deployment on Vercel is NOT yet verified. Do this on the first preview of the T02 branch.

## Env vars (Preview + Production)

- `INTERNAL_SERVICE_SECRET`: 32+ random bytes, same value for Next.js and Python (same project, so one var).
- `NEON_DATABASE_URL`: pooled URL (the Python service falls back to it when `DATABASE_URL` is unset; it uses `prepare_threshold=None`).
- `VERCEL_AUTOMATION_BYPASS_SECRET`: enable "Protection Bypass for Automation" in Project Settings > Deployment Protection; Vercel exposes the secret under this name automatically.

## Checks

1. Build log shows both the Next.js build and a Python function for `api/py/index.py` (Functions tab lists it). If Vercel does not detect Python alongside Next.js, or install of `requirements.txt` fails, the fallback in ADR-0002 applies: a second Vercel project rooted at `intelligence/`.
2. Function size is well under the 500 MB limit and `intelligence/tests` is not in the bundle (inspect the function's files in the build output; `excludeFiles` in `vercel.json`, `.vercelignore`).
3. Unauthenticated, no bypass header: expect 401 from Vercel's protection page (HTML), proving protection is on.
4. With bypass but no token:
   `curl -i -H "x-vercel-protection-bypass: $VERCEL_AUTOMATION_BYPASS_SECRET" https://<preview>/api/py/health` expect `401 {"detail":"unauthorized"}` (JSON from FastAPI, so routing and the rewrite reach Python).
5. With a minted token (scope `health`, 60 s; mint via a tiny script using `lib/internal-token.ts` or `jwt.encode` with `aud=scribe-intel`):
   `curl -H "x-vercel-protection-bypass: ..." -H "Authorization: Bearer $TOK" https://<preview>/api/py/health` expect 200 with `ok:true`, a `postgres_version`, and `pgvector_version` non-null on Neon.
6. Path handling: the app serves both `/health` and `/api/py/health`, so it works whether or not the rewrite preserves the `/api/py` prefix. Record which path FastAPI actually saw (add a temporary log) and drop the unused route if desired.
7. Expired token (wait 61 s) and wrong audience return 401.
8. Repeat step 5 five times in a row to confirm no prepared-statement errors through the Neon pooler (`prepared statement ... already exists`).
9. Cold start latency of `/api/py/health` and that the existing cron entry (`/api/internal/ingest/run`) is still listed under Settings > Cron Jobs.
10. From Next.js, `callIntelligence("/health", {sub, wid, scope: "health"})` (lib/intelligence-client.ts) works from a server route on the preview using the bypass header. `VERCEL_URL` is the deployment-specific host, which is protected; the bypass header handles that.

## Known unknowns

- `functions.maxDuration: 300` for a Python function on Hobby, and `includeFiles`/`excludeFiles` globs, are unverified against the current Vercel schema; the first deploy will reject invalid config.
- Whether root `requirements.txt` is honored when a root `package.json` exists.

# Runbook: promote Scribe 2.0 to production, and roll back

**Prepared, not executed.** Nothing in this file has been run against Neon or Vercel. Every command below touches remote infrastructure and is yours to run, one step at a time. After each step there is a **STOP: verify** line. Do not start the next step until its check holds. If a check fails, jump to "Rollback".

This runbook is the sequencing layer over [neon-rehearsal.md](neon-rehearsal.md) (the details, the scripts and the expected outputs of phases 1-12) and [T02-verification.md](T02-verification.md) (the preview checks). Read those first.

> **LOUD WARNING: Preview and Production share `NEON_DATABASE_URL` today.** Any branch preview, including a preview of a pull request, runs against the **production database**. Until step 1 is done, do not open a preview, do not run `scripts/smoke.ts --lifecycle` against one, and do not merge anything that runs migrations. A preview of 2.0 code on the production database is exactly how the 2026-10-12 incident happened (migrations 0008+ never ran; "relation workspaces does not exist").

## Facts to have in front of you

| Item | Value |
|---|---|
| Production domain | `scribe-marsh.vercel.app`, serving the pre-2.0 build `scribe-9wz4vw60w` (commit `0edddb1`). Auto-assign of production is paused. |
| Rollback build (1.0.1) | branch `rollback/1.0-compat`, commit `baa1634`. Works on the migrated schema only because migration 0020 is applied. |
| Migrations to run on production | 0008 through 0020, in journal order, by `scripts/neon-rehearsal/apply.sh` (never `scripts/apply-migrations.sh`, which starts with `DROP SCHEMA public CASCADE`). |
| Crons to avoid | 00:00 and 01:00 UTC (05:30 and 06:30 IST). |
| Flags at cutover | `INGEST_ENGINE=legacy`, `RETRIEVAL_ENGINE=legacy`, `FEATURE_BILLING_ENABLED` off. Flip to `python` later, preview first. |

## Gate: do not start until all are true

- [ ] The rehearsal on a Neon branch copy passed end to end (neon-rehearsal.md phases 1-11) and its results table is filled with a written Go decision.
- [ ] `INTERNAL_SERVICE_SECRET` exists in Vercel (it does not today) and `VOYAGE_API_KEY` is set if you want reranking.
- [ ] Preview has its own database (step 1).
- [ ] The preview smoke script passed (step 2).
- [ ] You know the Neon history-retention window (a restore is only possible inside it).

## Step 1. Give Preview its own database

```text
Vercel -> Project -> Settings -> Environment Variables
  NEON_DATABASE_URL  (Preview only)  = a NON-production Neon branch, pooled URL
  Leave the Production value untouched.
```

Create the preview branch from production (copy-on-write) with `npx neonctl@latest branches create --parent <prod branch> --name preview-scribe2`, run `scripts/neon-rehearsal/apply.sh` on it exactly as in neon-rehearsal.md phase 3, and use its pooled connection string.

**STOP: verify.** In the Vercel UI, the Preview and Production `NEON_DATABASE_URL` hosts (`ep-...`) are different. Redeploy a preview and open `/api/health`; then `psql "$PREVIEW_URL" -c "select count(*) from workspaces"` returns a number (the 2.0 schema is there) while the same query on production still fails (no `workspaces` table). If they are the same host, stop.

## Step 2. Preview smoke (ingest, retrieve, cite, update, delete)

Set `INGEST_ENGINE=python`, `RETRIEVAL_ENGINE=python` and `INTERNAL_SERVICE_SECRET` for Preview only. Sign in on the preview with a Clerk **development** user, copy its `__session` cookie value into a shell variable (never into a file or a chat), then:

```bash
export SCRIBE_SESSION_TOKEN='...'
npx tsx scripts/smoke.ts --base-url https://<preview-host> --lifecycle --attempts 40 --wait-ms 3000
```

It walks: health, the Python auth guard, upload, ask with a citation, open the citation, upload version 2 and see the new value cited at version 2, delete, and see the citation withheld and the answers stop citing the Source. `SKIP` lines are **not** passes (the update steps skip when the deployment is not on the Python engine).

Also run the manual parts of [T02-verification.md](T02-verification.md) steps 1-10 once (Python co-deploys, bypass header, token expiry, five health calls in a row, cron still listed).

**STOP: verify.** Every smoke line says `PASS`; none says `FAIL`; if any says `SKIP`, you have decided in writing that this is acceptable. Production rows are untouched (check the row count of `files` on production did not change).

## Step 3. Prepare the rollback build (no traffic)

```bash
git worktree add ../scribe-1.0.1 rollback/1.0-compat
cd ../scribe-1.0.1 && npm ci
vercel deploy --prod --skip-domain          # builds a production-target deployment WITHOUT assigning the domain
```

Write the printed deployment URL next to the 2.0 one in your notes. It is harmless against the unmigrated production database and serves no traffic until promoted.

**STOP: verify.** `vercel ls` shows the new deployment as Production-target but **not** attached to `scribe-marsh.vercel.app`; the domain still serves `scribe-9wz4vw60w`.

## Step 4. Restore point

```bash
npx neonctl@latest branches create --project-id "$NEON_PROJECT" --parent <prod branch> --name "pre-scribe2-$(date +%Y%m%d)"
export T0=$(date -u +%Y-%m-%dT%H:%M:%SZ); echo "$T0"
```

**STOP: verify.** The branch exists in the Neon console; `$T0` is recorded; you are inside the history-retention window for `$T0`; it is not within 10 minutes of 00:00 or 01:00 UTC.

## Step 5. Migrate and promote, back to back

The window between migration 0008 committing and 0020 committing is the only time the running 1.0 build cannot create files, chats or traces. Keep it short: have the promote command typed and ready before you start.

```bash
# in the 2.0 checkout, on the commit you rehearsed
node scripts/neon-rehearsal/check-journal.mjs .                           # offline; must report no problems
psql "$PROD_URL" -X -f scripts/neon-rehearsal/precheck.sql | tee precheck-prod.txt   # compare with the branch rehearsal
PROD_HOST="<prod ep- host>" ALLOW_PROD=1 CONFIRM_HOST="<prod ep- host>" scripts/neon-rehearsal/apply.sh "$PROD_URL"   # PROD_URL = the DIRECT (non -pooler) URL
vercel promote <2.0 deployment url>                                       # immediately after apply.sh prints "all 13 files applied"
```

If `apply.sh` fails on a file, do **not** debug live. Files 0008-0013 failing leaves 1.0 broken: go straight to Rollback (code-only is not enough, see below). If 0013 committed and the failure is in 0014-0019, applying 0020 alone revives 1.0 (command in neon-rehearsal.md phase 12 step 3); still roll back or fix on a branch.

**STOP: verify (within 2 minutes).**

```bash
psql "$PROD_URL" -X -f scripts/neon-rehearsal/postcheck.sql | tee postcheck-prod.txt   # every section meets its EXPECT line
curl -s https://scribe-marsh.vercel.app/api/health                        # "ok":true and "db":"connected"
```

then in a browser: sign in, open an existing chat, ask a question, open `/upload`. Existing chats and files must be visible (they were backfilled into each user's personal workspace). Any database error in the Vercel runtime logs: Rollback now.

## Step 6. Journal, backfill, smoke on production

```bash
PROD_HOST="<prod ep- host>" ALLOW_PROD=1 CONFIRM_HOST="<prod ep- host>" scripts/neon-rehearsal/apply.sh "$PROD_URL" --register-journal
NEON_DATABASE_URL="$PROD_URL" npx --yes tsx scripts/backfill-legacy-generations.ts --dry-run
NEON_DATABASE_URL="$PROD_URL" npx --yes tsx scripts/backfill-legacy-generations.ts
NEON_DATABASE_URL="$PROD_URL" npx --yes tsx scripts/backfill-legacy-generations.ts      # second run must report 0 files
psql "$PROD_URL" -X -f scripts/neon-rehearsal/postcheck.sql                              # section 7 now passes too
```

**STOP: verify.** The second backfill run reports `filesSeen 0`; postcheck section 7 shows 0 `without_version` and 0 `without_active_legacy_index` for ready files. Run the legacy-engine parts of the smoke (`scripts/smoke.ts` without `--lifecycle`) against production with a real account.

## Step 7. Engines (separate decisions, days apart)

1. Set `INGEST_ENGINE=python` on the **preview** first and repeat the lifecycle smoke. Only then set it on production. Once it is `python`, a code-only rollback is no longer clean (limit b below).
2. Set `RETRIEVAL_ENGINE=python` only after the golden-set review and the lexical evaluation in `HUMAN_TODO.md`; the answers change.

**STOP: verify after each flip.** Smoke passes; the Vercel logs show no `ingest step failed`; the daily sweep route answers 200.

## Rollback

Choose by what went wrong.

**A. 2.0 misbehaves, data is fine (code-only, keeps data).** Needs 0020 applied.

```bash
vercel promote <1.0.1 deployment url from step 3>
```

Limits: (a) 1.0 ignores `files.deleted_at`, so Sources soft-deleted under 2.0 reappear until purged; (b) Sources ingested by the Python engine have no `documents` rows, so 1.0 cannot search them (clean only while 2.0 ran with `INGEST_ENGINE=legacy`); (c) the old `scribe-9wz4vw60w` cannot accept uploads on the migrated schema, so promote it only as a last resort; (d) keep 0020 applied for as long as 1.0.1 is a rollback target.

**STOP: verify.** Sign in, create a chat, ask a question, upload a small file; the file leaves `uploading`.

**B. A migration failed or data is damaged (loses data written after `T0`).**

```bash
vercel promote scribe-9wz4vw60w
npx neonctl@latest branches restore <prod branch> "^self@$T0" --project-id "$NEON_PROJECT" --preserve-under-name "prod-before-restore"
psql "$PROD_URL" -X -f scripts/neon-rehearsal/precheck.sql     # section 4 shows no 2.0 objects again
```

If `T0` is outside the retention window, restore from the `pre-scribe2-<date>` branch instead. **STOP: verify** that sign-in, chats and files work on the old build, and tell affected users that anything written after `T0` is gone.

## After it is stable

Delete the rehearsal and preview branches you no longer need (`neonctl branches delete`), keep `pre-scribe2-<date>` for the retention window, record the actual deployment SHA, flags, role and migration journal in `completion-plan.md` and `HANDOVER.md`, and fill the results table in neon-rehearsal.md phase 11.

# Neon branch migration rehearsal (Scribe 1.0 → 2.0 schema)

Production (`scribe-marsh.vercel.app`) still serves the pre-2.0 build `scribe-9wz4vw60w` on a database that has never seen migrations 0008+. This runbook proves that the migrations, the legacy backfill and the 2.0 app work on a **copy** of production before anything touches production. It is the "Owner steps" of HANDOVER section 11 turned into commands.

Nothing here writes to the production database. Phases 1-9 only touch a throwaway Neon branch; phase 10 (cutover) is a separate decision.

Scripts: `scripts/neon-rehearsal/`

| File | What it does |
|---|---|
| `precheck.sql` | Read-only facts the migrations depend on (PG version, role attributes, pgvector, drizzle journal, rows 0008/0009 touch). |
| `apply.sh` | Applies every `drizzle/00NN_*.sql` with NN ≥ 08, one transaction per file, timed. Refuses unless `CONFIRM_HOST` equals the URL's host, and refuses a `-pooler` host. `--register-journal` records the files in `drizzle.__drizzle_migrations` instead. |
| `postcheck.sql` | Read-only checks after migration and after the backfill, each with its expected result. |
| `fingerprint.sql` | Order-independent schema fingerprint for diffing the upgraded branch against a fresh 2.0 schema. |

**Never run `scripts/apply-migrations.sh` against Neon.** It starts with `DROP SCHEMA public CASCADE`.

## Why psql and not `drizzle-kit migrate`

- The project only installs the Neon serverless driver, which drizzle-kit would use. Migrations 0008-0012 are multi-statement files with no `--> statement-breakpoint`, and the Neon HTTP path rejects several statements in one query. The psql path is the one the test suite and CI exercise.
- Production may have been built with `drizzle-kit push`, in which case there is no `drizzle.__drizzle_migrations` table and `migrate` would try to replay 0000-0007 (not replayable). Precheck section 3 tells you which case you are in. `apply.sh --register-journal` then writes the rows `migrate` expects (sha256 of the file, `created_at` = journal `when`), so future migrations can use `drizzle-kit migrate` again.

## Already verified locally (2026-10-19, main @ `502085d`)

On a clean pgvector pg17 container, using the schema exported from the 1.0 commit `0edddb1`, loaded and migrated by a `NOSUPERUSER CREATEROLE NOBYPASSRLS` database owner (the shape of a Neon owner role), with seeded users, files, chats, traces and 3072-d documents:

- `apply.sh` applied 0008-0019 in 2 s, and again in 1 s (idempotent).
- Every postcheck section passed: workspaces = distinct users, no owner mismatches, RLS on with FORCE off on all 23 tables, all `app_*` functions are SECURITY DEFINER owned by `scribe_definer`, `scribe_definer` has no CREATE on `public`, no invalid indexes.
- Fingerprint diff against a fresh 2.0 schema (`scripts/apply-migrations.sh`) is exactly these lines, both harmless. **Any other line on the Neon branch is production drift and must be explained before cutover.**
  ```
  > constraint workspaces CHECK ((kind = ANY (ARRAY['personal'::text, 'eval'::text])))
  < index CREATE UNIQUE INDEX workspace_members_workspace_id_user_id_pk ON public.workspace_members ...
  > index CREATE UNIQUE INDEX workspace_members_pkey ON public.workspace_members ...
  ```
- `--register-journal` ran twice without duplicating rows (12 rows, last `created_at` 1784851200000).
- `backfill-legacy-generations.ts` (psql path) created version 1 and an active `legacy-v1` generation for every ready file. A re-run found 0 files. The Python `scribe_ingest.upgrade backfill --dry-run` ran.

What the local run cannot cover, and is the point of this rehearsal: Neon's Postgres version and owner-role privileges, Neon's pgvector version, the real production data and journal, the **Neon HTTP path of the TS backfill (never run)**, and the 2.0 app running on Neon.

Found while preparing: **if roles `scribe_app` / `scribe_definer` already exist and the migrating role has no ADMIN option on them, 0013 fails** with `permission denied to grant role "scribe_definer"`. Precheck section 4 shows whether either role exists.

## Phase 0: local preparation (no Neon, no Vercel)

```bash
cd SCRIBE && git switch main && git pull --ff-only     # rehearse exactly what will ship
npm ci
export OUT=~/scribe-rehearsal-$(date +%Y%m%d) && mkdir -p "$OUT"   # outside the repo: outputs describe production data
```

Build the local reference schema for the diff in phase 4 (Docker test DB, name must contain `test`):

```bash
docker compose up -d db
PGPASSWORD=scribe psql -h localhost -p 54329 -U scribe -d postgres -c 'CREATE DATABASE scribe_ref_test'
scripts/apply-migrations.sh postgresql://scribe:scribe@localhost:54329/scribe_ref_test
psql postgresql://scribe:scribe@localhost:54329/scribe_ref_test -X -At -f scripts/neon-rehearsal/fingerprint.sql > "$OUT/fp-ref.txt"
```

## Phase 1: create the Neon branch (first Neon action, needs your OK)

1. Find the production branch. In Vercel → Settings → Environment Variables, look at the **host** of the Production `NEON_DATABASE_URL` (`ep-…`). Do not copy the password anywhere.
2. In the Neon console (or `npx neonctl@latest auth` once), find the project and the branch whose compute endpoint is that `ep-…` host:
   ```bash
   npx neonctl@latest projects list
   npx neonctl@latest branches list --project-id "$NEON_PROJECT"
   npx neonctl@latest endpoints list --project-id "$NEON_PROJECT"
   ```
3. Create a copy-on-write branch from it (instant; reads production's data as of now, writes go only to the branch):
   ```bash
   export BRANCH=scribe2-rehearsal-$(date +%Y%m%d)
   npx neonctl@latest branches create --project-id "$NEON_PROJECT" --parent <prod branch name> --name "$BRANCH"
   export BRANCH_URL="$(npx neonctl@latest connection-string "$BRANCH" --project-id "$NEON_PROJECT" \
     --role-name <role from the prod URL> --database-name <db from the prod URL>)"
   ```
   The default connection string is the direct (non-pooler) endpoint, which migrations need.
4. Sanity check: the host in `$BRANCH_URL` is **not** the production host. `apply.sh` asks you to type it in step 3.

The branch holds a full copy of production user data. Keep it private and delete it in phase 9.

## Phase 2: precheck

```bash
psql "$BRANCH_URL" -X -f scripts/neon-rehearsal/precheck.sql | tee "$OUT/precheck.txt"
```

| Section | Pass | If not |
|---|---|---|
| 1 version | `version_num` ≥ 150000 (16 or 17 expected on Neon) | < 15: 0019 skips the SET NULL composite FKs with a NOTICE; decide whether that is acceptable |
| 1 role | `rolcreaterole = t` | 0013 cannot create `scribe_app` / `scribe_definer`. Use a role that has it (Neon's owner role is a member of `neon_superuser`) |
| 1 role | note `rolbypassrls` | `t` is expected on Neon: RLS is then **not enforced** for this connection (HANDOVER 11.3). Not a blocker for cutover; phase 7 tests real enforcement |
| 2 pgvector | `installed_version` set | 0010 aborts. If < 0.8.0, keep `dense_mode=exact` |
| 3 journal | last `created_at` = 1783209600000 (0007), or no journal | A larger value means part of 2.0 already ran on production: stop and investigate |
| 4 objects | no rows in all three queries | Any 2.0 table/column already present means a partial earlier run. A pre-existing role needs ADMIN for the migrating role (see "Found while preparing") |
| 5 rows | `null_user_id` = 0 everywhere | 0008 fails at `SET NOT NULL`. Fix or delete those rows first (on the branch, then the same on prod) |
| 5 rows | note `files_0009_will_mark_failed` | These in-flight legacy uploads become "failed, please upload again". Tell affected users if the count is not 0 |
| 6 sizes | note them | Large `documents` means a slower backfill; time it in phase 6 |

## Phase 3: apply the migrations (twice)

```bash
host=$(printf '%s' "$BRANCH_URL" | sed -E 's#^[a-z]+://([^@]*@)?([^/:?]+).*#\2#'); echo "$host"
CONFIRM_HOST="$host" scripts/neon-rehearsal/apply.sh "$BRANCH_URL" | tee "$OUT/apply-1.txt"
CONFIRM_HOST="$host" scripts/neon-rehearsal/apply.sh "$BRANCH_URL" | tee "$OUT/apply-2.txt"   # idempotency
```

Pass: both runs end with `all 12 files applied`. Record per-file seconds: their sum is roughly the production write-lock window.

On failure: the failing file rolled back as a whole (one transaction per file), earlier files stay applied. Save the error, then either fix and re-run (the files are idempotent) or start over from production's state:

```bash
npx neonctl@latest branches reset "$BRANCH" --project-id "$NEON_PROJECT" --parent
```

## Phase 4: postcheck and schema diff

```bash
psql "$BRANCH_URL" -X -f scripts/neon-rehearsal/postcheck.sql | tee "$OUT/postcheck-1.txt"
psql "$BRANCH_URL" -X -At -f scripts/neon-rehearsal/fingerprint.sql > "$OUT/fp-branch.txt"
diff "$OUT/fp-ref.txt" "$OUT/fp-branch.txt" | tee "$OUT/fp.diff"
```

Pass: sections 1-6 match their EXPECT lines, and `fp.diff` holds only the three known lines (above). Extra lines are columns, indexes or constraints production has (or lacks) compared with what the 1.0 code declares. Each one needs a decision, either a follow-up migration or "harmless, recorded".

## Phase 5: drizzle journal

```bash
CONFIRM_HOST="$host" scripts/neon-rehearsal/apply.sh "$BRANCH_URL" --register-journal | tee "$OUT/journal.txt"
NEON_DATABASE_URL="$BRANCH_URL" npx drizzle-kit migrate 2>&1 | tee "$OUT/drizzle-migrate.txt"   # branch only
```

Pass: the last journal row has `created_at` 1784851200000, and `drizzle-kit migrate` applies nothing. If it tries to apply anything, it is replaying old history: let it fail on the branch, record it, and never run `drizzle-kit migrate` against production. Future migrations then also go through `apply.sh`.

## Phase 6: legacy backfill (first real run of the Neon HTTP path)

```bash
NEON_DATABASE_URL="$BRANCH_URL" npx --yes tsx scripts/backfill-legacy-generations.ts --dry-run | tee "$OUT/backfill-dry.txt"
time NEON_DATABASE_URL="$BRANCH_URL" npx --yes tsx scripts/backfill-legacy-generations.ts | tee "$OUT/backfill-1.txt"
NEON_DATABASE_URL="$BRANCH_URL" npx --yes tsx scripts/backfill-legacy-generations.ts | tee "$OUT/backfill-2.txt"   # expect filesSeen 0
psql "$BRANCH_URL" -X -f scripts/neon-rehearsal/postcheck.sql | tee "$OUT/postcheck-2.txt"
uv run --project intelligence python -m scribe_ingest.upgrade --database-url "$BRANCH_URL" backfill --dry-run | tee "$OUT/upgrade-dry.txt"
```

Pass: postcheck section 7 shows 0 `without_version` and 0 `without_active_legacy_index` for `ready` files (failed files without documents legitimately have no index), and `legacy_3072_embeddings` = `legacy_documents` for those files. The second TS run sees 0 files. The Python upgrade stays a **dry run**: a real run queues structural-v1 jobs that spend Gemini embedding calls, a post-cutover decision. Its dry run shows how many Sources would be queued and how many would be marked "re-upload" (no readable blob).

## Phase 7: RLS enforcement as `scribe_app` (branch only, optional but recommended)

```bash
PW=$(openssl rand -hex 24)
psql "$BRANCH_URL" -X -c "ALTER ROLE scribe_app LOGIN PASSWORD '$PW'"
APP_URL=$(printf '%s' "$BRANCH_URL" | sed -E "s#://[^:]+:[^@]+@#://scribe_app:$PW@#")
WS=$(psql "$BRANCH_URL" -X -At -c "SELECT workspace_id FROM files GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")
psql "$APP_URL" -X -c "SELECT count(*) AS no_context FROM files"                                    # expect 0
psql "$APP_URL" -X -c "SELECT set_config('app.workspace_id', '$WS', false); SELECT count(DISTINCT workspace_id) AS workspaces_seen, count(*) FROM files"   # expect 1 workspace
psql "$BRANCH_URL" -X -c "ALTER ROLE scribe_app NOLOGIN PASSWORD NULL"
```

Pass: zero rows without a context, and only that workspace's rows with it. This is the first evidence that database-enforced isolation works on Neon. Production keeps the owner connection until callers move to `scribe_app`.

## Phase 8: run the 2.0 app against the branch (local, legacy engines)

Point a local dev server at the branch's **pooled** URL (`connection-string … --pooled`), with `INGEST_ENGINE=legacy` and `RETRIEVAL_ENGINE=legacy` (the cutover settings), plus Clerk development keys and the Gemini key. Those live in Vercel's Development environment: `vercel env pull` reads them, so whether to run it is your call. Then:

- sign in, upload a small PDF, wait for `ready`, ask a question that cites it, delete it;
- open `/api/py/health` via the dev server if the Python service is running locally (`postgres_version`, `pgvector_version` from Neon).

Your Clerk development users are not the production users, so you start with an empty personal workspace. That is expected. Production rows are covered by postchecks 3 and 7.

Do **not** test this through a Vercel preview yet: Preview and Production share `NEON_DATABASE_URL` today, so a preview would run 2.0 code against the production database.

## Phase 9: record and clean up

1. Fill the results table below and keep `$OUT` (it holds row counts, not document content).
2. Delete the branch: `npx neonctl@latest branches delete "$BRANCH" --project-id "$NEON_PROJECT"`.
3. Locally: `PGPASSWORD=scribe psql -h localhost -p 54329 -U scribe -d postgres -c 'DROP DATABASE scribe_ref_test'`.

| Check | Result |
|---|---|
| Neon PG version / pgvector version | |
| Migrating role: createrole / bypassrls | |
| Journal state before (none / last = 0007 / other) | |
| apply run 1 / run 2 (total seconds) | |
| postcheck 1-6 all pass | |
| fingerprint diff = the 3 known lines | |
| drizzle-kit migrate after registration = no-op | |
| TS backfill seconds, files, re-run = 0 | |
| upgrade dry-run: would queue / would mark re-upload | |
| RLS as scribe_app (phase 7) | |
| app smoke (phase 8) | |
| **Go / no-go** | |

**Go** = every row passes, or each deviation has a written decision.

## Phase 10: production cutover (separate approval, not part of the rehearsal)

Outline only. Each step touches production and needs its own OK.

1. **Before the window.** Give Preview its own database (`NEON_DATABASE_URL` for Preview → a non-production Neon branch), add `INTERNAL_SERVICE_SECRET` to Vercel (missing today), and run `docs/scribe2/T02-verification.md` on a preview. Identify the deployment built from the `main` commit you rehearsed. Production auto-assign stays paused.
2. **Restore point.** Create a branch `pre-scribe2-<date>` from production immediately before migrating.
3. **Window.** Avoid the crons (00:00 and 01:00 UTC = 05:30 and 06:30 IST). Run `precheck.sql` on production and compare it with the branch, run `apply.sh`, `postcheck.sql`, `--register-journal`, then **promote the 2.0 deployment immediately** (`vercel promote <deployment url>`). From the moment 0008 commits, the running 1.0 build cannot create files, chats or RAG traces: its inserts omit the new NOT NULL `workspace_id`. Keep the gap to minutes.
4. **After promote.** Run the TS legacy backfill, do the phase 8 smoke on production, and watch logs. Flip `INGEST_ENGINE=python` on a preview first, then production.
5. **Rollback.** `vercel promote` the old deployment `scribe-9wz4vw60w`, and restore the production branch from the `pre-scribe2-<date>` point in the Neon console. Data written after the restore point is lost. Down files exist only for 0013-0019 (`drizzle/rollback/`); 0008-0012 have none, so the Neon restore is the real rollback.

# Neon branch migration rehearsal (Scribe 1.0 → 2.0 schema)

Production (`scribe-marsh.vercel.app`) still serves the pre-2.0 build `scribe-9wz4vw60w` on a database that has never seen migrations 0008+. This runbook proves that the migrations, the legacy backfill and the 2.0 app work on a **copy** of production before anything touches production. It is the "Owner steps" of HANDOVER section 11 turned into commands.

Nothing here writes to the production database. Phases 1-11 only touch a throwaway Neon branch; phase 12 (cutover) is a separate decision.

Migration 0020 is a compatibility shim: a trigger on `files`, `chats` and `rag_traces` that fills in the user's personal `workspace_id` when the Scribe 1.0 build inserts a row without one. Without it the 1.0 build breaks the moment 0008 commits, and rolling back means restoring the database. With it, promoting a 1.0 build is a valid rollback (with limits, see phase 12, step 5), and phase 9 rehearses exactly that: the 1.0.1 build running against the migrated branch. The rollback build is 1.0.1, branch `rollback/1.0-compat` (commit `baa1634`) = `0edddb1` + a one-line change to the upload-completion SQL (see phase 9). The unpatched 1.0 build cannot accept uploads once 0008-0020 are applied.

Scripts: `scripts/neon-rehearsal/`

| File | What it does |
|---|---|
| `precheck.sql` | Read-only facts the migrations depend on (PG version, role attributes, pgvector, drizzle journal, rows 0008/0009 touch). |
| `apply.sh` | Applies every `drizzle/00NN_*.sql` with NN ≥ 08 (0008-0020, 13 files), one transaction per file, timed. Refuses unless `CONFIRM_HOST` equals the URL's host, and refuses a `-pooler` host. `--register-journal` records the files in `drizzle.__drizzle_migrations` instead. |
| `postcheck.sql` | Read-only checks after migration and after the backfill, each with its expected result. |
| `fingerprint.sql` | Order-independent schema fingerprint for diffing the upgraded branch against a fresh 2.0 schema. |

**Never run `scripts/apply-migrations.sh` against Neon.** It starts with `DROP SCHEMA public CASCADE`.

## Why psql and not `drizzle-kit migrate`

- The project only installs the Neon serverless driver, which drizzle-kit would use. Migrations 0008-0012 are multi-statement files with no `--> statement-breakpoint`, and the Neon HTTP path rejects several statements in one query. The psql path is the one the test suite and CI exercise.
- Production may have been built with `drizzle-kit push`, in which case there is no `drizzle.__drizzle_migrations` table and `migrate` would try to replay 0000-0007 (not replayable). Precheck section 3 tells you which case you are in. `apply.sh --register-journal` then writes the rows `migrate` expects (sha256 of the file, `created_at` = journal `when`), so future migrations can use `drizzle-kit migrate` again.

## Already verified locally (2026-10-19, main @ `502085d`)

On a clean pgvector pg17 container, using the schema exported from the 1.0 commit `0edddb1`, loaded and migrated by a `NOSUPERUSER CREATEROLE NOBYPASSRLS` database owner (the shape of a Neon owner role), with seeded users, files, chats, traces and 3072-d documents:

- `apply.sh` applied 0008-0019 in 2 s, and again in 1 s (idempotent). 0020 did not exist yet. It is covered by `scripts/test-migrations-nosuperuser.sh` (first apply and re-apply as a `NOSUPERUSER CREATEROLE` owner on pg17) and `intelligence/tests/integration/test_legacy_workspace_default.py`, not by these numbers and not on Neon.
- Re-run with 0020 (2026-10-19, same setup, smaller seed): `apply.sh` applied all 13 files twice (under 1 s each run). As the owner with no workspace context, 1.0-shaped inserts (chats, chat_messages, files, rag_traces with 1.0's column sets) landed in the personal workspace, both for a user backfilled by 0008 and for a new user. Each user had one personal workspace and one owner membership. Postcheck section 6b listed the three triggers, enabled.
- 1.0.1 upload statement (2026-10-20, Docker pg17, throwaway DB `scribe_v101_test`): on the pure 1.0 schema (drizzle-kit export of `lib/db-schema.ts` at `0edddb1`) and on the same DB after `apply.sh` (0008-0020), the exact 1.0.1 `transitionToQueued` statement (bare `ON CONFLICT DO NOTHING`) moved a 1.0-shaped file from `uploading` to `queued` and inserted one `ingestion_jobs` row, and a second insert for the same file inserted nothing (1 row). The unpatched `0edddb1` statement still fails on the migrated schema with the known `there is no unique or exclusion constraint matching the ON CONFLICT specification`. The branch `rollback/1.0-compat` builds (vitest, tsc, lint, `next build`); not run against a real browser or Blob.
- Every postcheck section passed: workspaces = distinct users, no owner mismatches, RLS on with FORCE off on all 23 tables, all `app_*` functions are SECURITY DEFINER owned by `scribe_definer`, `scribe_definer` has no CREATE on `public`, no invalid indexes.
- Fingerprint diff against a fresh 2.0 schema (`scripts/apply-migrations.sh`) is exactly these lines, both harmless. **Any other line on the Neon branch is production drift and must be explained before cutover.**
  ```
  > constraint workspaces CHECK ((kind = ANY (ARRAY['personal'::text, 'eval'::text])))
  < index CREATE UNIQUE INDEX workspace_members_workspace_id_user_id_pk ON public.workspace_members ...
  > index CREATE UNIQUE INDEX workspace_members_pkey ON public.workspace_members ...
  ```
- `--register-journal` ran twice without duplicating rows (12 rows, last `created_at` 1784851200000). With 0020 expect 13 rows and last `created_at` 1784937600000.
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

1. Find the production branch. In Vercel → Settings → Environment Variables, look at the **host** of the Production `NEON_DATABASE_URL` (`ep-…`). Do not copy the password anywhere. Keep the host: `export PROD_HOST=ep-…` (with or without `-pooler`). `apply.sh` refuses it unless `ALLOW_PROD=1`.
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

Run every later phase in this same shell, and start each one with this guard. Phases 5-10 write, and only this line tells them they are on the branch:

```bash
: "${BRANCH_URL:?}" "${OUT:?}" "${PROD_HOST:?}"; case "$BRANCH_URL" in *"${PROD_HOST/-pooler./.}"*|*"$PROD_HOST"*) echo "STOP: BRANCH_URL is production";; *) echo "ok: branch";; esac
```

The branch holds a full copy of production user data. Keep it private and delete it in phase 11.

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

Note the time first; phase 10 restores the branch to it, as a rehearsal of the Neon restore.

```bash
export T0=$(date -u +%Y-%m-%dT%H:%M:%SZ); echo "$T0" | tee "$OUT/t0.txt"
host=$(printf '%s' "$BRANCH_URL" | sed -E 's#^[a-z]+://([^@]*@)?([^/:?]+).*#\2#'); echo "$host"
CONFIRM_HOST="$host" scripts/neon-rehearsal/apply.sh "$BRANCH_URL" | tee "$OUT/apply-1.txt"
CONFIRM_HOST="$host" scripts/neon-rehearsal/apply.sh "$BRANCH_URL" | tee "$OUT/apply-2.txt"   # idempotency
```

Pass: both runs end with `all 13 files applied`. Record per-file seconds: their sum is roughly the production write-lock window.

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

Pass: sections 1-6 match their EXPECT lines (section 6b lists the three `*_legacy_default_workspace` triggers, all `O` = enabled), and `fp.diff` holds only the three known lines (above). Extra lines are columns, indexes or constraints production has (or lacks) compared with what the 1.0 code declares. Each one needs a decision, either a follow-up migration or "harmless, recorded".

## Phase 5: drizzle journal

```bash
CONFIRM_HOST="$host" scripts/neon-rehearsal/apply.sh "$BRANCH_URL" --register-journal | tee "$OUT/journal.txt"
NEON_DATABASE_URL="$BRANCH_URL" npx drizzle-kit migrate 2>&1 | tee "$OUT/drizzle-migrate.txt"   # branch only
```

Pass: the last journal row has `created_at` 1784937600000 (0020), and `drizzle-kit migrate` applies nothing. If it tries to apply anything, it is replaying old history: let it fail on the branch, record it, and never run `drizzle-kit migrate` against production. Future migrations then also go through `apply.sh`.

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
- if the Python service runs locally, call `/health` with a minted `health` token (T02 checklist step 5) and check that `postgres_version` and `pgvector_version` come from Neon.

Your Clerk development users are not the production users, so you start with an empty personal workspace. That is expected. Production rows are covered by postchecks 3 and 7.

Do **not** test this through a Vercel preview yet: Preview and Production share `NEON_DATABASE_URL` today, so a preview would run 2.0 code against the production database.

## Phase 9: run the 1.0 build against the migrated branch (rehearses the code-only rollback)

This proves that the 1.0.1 rollback build (branch `rollback/1.0-compat`, commit `baa1634`, which is `0edddb1` plus the one-line upload fix and a test) still works on the migrated schema because of 0020 and that fix. Run it after phases 3-6 (migrated, journal registered, backfilled) and before the restore in phase 10. The branch must exist locally (or be pushed, then use `origin/rollback/1.0-compat`).

```bash
git worktree add ../scribe-1.0.1 rollback/1.0-compat
cd ../scribe-1.0.1 && npm ci
# 1.0 variable names (.env.example in that checkout). Values: Clerk DEVELOPMENT keys, the Gemini key, a Blob token from the
# Vercel Development environment (not the Production one, so test uploads do not land in production storage), and the
# FEATURE_* / ENABLE_SUPERMEMORY flags as Production has them. Use the branch's POOLED url, as production does.
NEON_DATABASE_URL="<branch pooled url>" GOOGLE_GENERATIVE_AI_API_KEY=... BLOB_READ_WRITE_TOKEN=... UPLOAD_SIGNING_SECRET=... \
CRON_SECRET=... NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=... CLERK_SECRET_KEY=... NEXT_PUBLIC_APP_URL=http://localhost:3001 \
FEATURE_ASYNC_INGESTION_ENABLED=... FEATURE_BILLING_ENABLED=... FEATURE_PUBLIC_LANDING_ENABLED=... ENABLE_SUPERMEMORY=... \
npm run dev -- -p 3001
```

In the browser on `http://localhost:3001`: sign in, create a chat, ask a question that makes it search your documents, and upload a small file. In 1.0 the browser calls `POST /api/sources/reserve` (creates the `files` row as `uploading` and returns a client token), uploads straight to Vercel Blob, and then Vercel Blob calls `POST /api/sources/upload-complete` (`onUploadCompleted` -> `completeSourceUpload` in `lib/ingestion/source-intake.ts`), which flips the file to `queued` and inserts the `ingestion_jobs` row, the statement 1.0.1 changed. The browser then calls `POST /api/sources/process-now`, which only claims jobs that already exist. Blob cannot reach `localhost`, so trigger the completion one of two ways:

- **Tunnel (closest to production).** Start a tunnel to port 3001 (for example `cloudflared tunnel --url http://localhost:3001`) and start the dev server with `NEXT_PUBLIC_APP_URL=<tunnel url>`. The callback URL is built from it at reservation time, so set it before you upload.
- **Replay the callback yourself.** Upload from the browser as above (the file stays `uploading`), then post the callback body signed the way Blob signs it: header `x-vercel-signature` = hex HMAC-SHA256 of the exact JSON body string, keyed with `BLOB_READ_WRITE_TOKEN`. The body is `{"type":"blob.upload-completed","payload":{"blob":{"url":"<blob url>","pathname":"<uploadPath from the reserve response>"},"tokenPayload":"{\"sourceId\":<files.id>,\"userId\":\"<$U>\"}"}}`. The blob must really exist (the handler reads its metadata), so the browser upload has to have finished. Get the blob url and `uploadPath` from the browser network tab.

Then check where those rows landed (`$U` = the Clerk user id of the user you signed in with):

```bash
U=$(psql "$BRANCH_URL" -X -At -c "SELECT user_id FROM chats ORDER BY created_at DESC LIMIT 1")   # newest chat = the one you just made
psql "$BRANCH_URL" -X -v u="$U" <<'SQL'
WITH w AS (SELECT id FROM workspaces WHERE kind = 'personal' AND owner_user_id = :'u')
SELECT 'personal_workspaces' AS item, count(*) AS n, count(*) AS n_in_personal_ws FROM w
UNION ALL SELECT 'owner_member', count(*), count(*) FROM workspace_members m WHERE m.user_id = :'u' AND m.role = 'owner' AND m.workspace_id IN (SELECT id FROM w)
UNION ALL SELECT 'files', count(*), count(*) FILTER (WHERE workspace_id IN (SELECT id FROM w)) FROM files WHERE user_id = :'u'
UNION ALL SELECT 'chats', count(*), count(*) FILTER (WHERE workspace_id IN (SELECT id FROM w)) FROM chats WHERE user_id = :'u'
UNION ALL SELECT 'rag_traces', count(*), count(*) FILTER (WHERE workspace_id IN (SELECT id FROM w)) FROM rag_traces WHERE user_id = :'u';
SQL
```

Then check the upload completion (`$F` = the uploaded file's id, `files.id`):

```bash
psql "$BRANCH_URL" -X -v f="$F" -c "SELECT (SELECT status FROM files WHERE id = :f) AS file_status, (SELECT count(*) FROM ingestion_jobs WHERE file_id = :f) AS job_rows"
```

Then start the 2.0 dev server as in phase 8 with the same Clerk development keys, sign in as the same user, and check that the chat and the uploaded file are listed.

Clean up: stop the 1.0.1 dev server and any tunnel, then `cd - && git worktree remove --force ../scribe-1.0.1`.

Pass, all of:
1. Sign-in, creating a chat and asking a question work in 1.0.1 with no database error in the dev server log, and the upload reservation returns a source id.
2. In the query output `personal_workspaces` and `owner_member` are 1 / 1, and for `files` and `chats` (and `rag_traces`, if the model searched) `n` is greater than 0 and `n` = `n_in_personal_ws`.
3. The 2.0 dev server shows the same user's chat and file.
4. After the completion ran (tunnel or replay), `file_status` is `queued` or later (never `uploading`), `job_rows` is exactly 1, and the dev server log has no `no unique or exclusion constraint` error.

**Why 1.0.1 exists.** 1.0's upload completion (`lib/ingestion/source-intake.ts`, run by the Blob callback) did `INSERT INTO ingestion_jobs ... ON CONFLICT ("file_id") DO NOTHING`. 0014 dropped the unique index on `ingestion_jobs(file_id)` and replaced it with one on `(file_id, COALESCE(source_version_id, zero-uuid))`, because 2.0 needs one job per source version, so on the migrated schema that statement fails with `there is no unique or exclusion constraint matching the ON CONFLICT specification` and a 1.0 upload never leaves `uploading`. The index cannot come back. The 1.0.1 change is that one line: `ON CONFLICT DO NOTHING`, with no column list. On the 1.0 schema the `file_id` unique index arbitrates, as before. On the migrated schema the `(file_id, COALESCE(version))` index does, a 1.0 job has a NULL version, and so a second insert for the same file still does nothing. Once 0008 is applied, promote the unpatched deployment `scribe-9wz4vw60w` (`0edddb1`) only as a last resort (phase 12, step 5, limit c): chat and search work but every upload fails.

## Phase 10: rehearse the Neon restore

The Neon point-in-time restore stays the fallback rollback: it is the only option if a migration fails or corrupts data, or if phase 9 fails. Practise it here. Check the flags with `npx neonctl@latest branches restore --help` first:

```bash
npx neonctl@latest branches restore "$BRANCH" "^self@$T0" --project-id "$NEON_PROJECT" --preserve-under-name "$BRANCH-migrated"
psql "$BRANCH_URL" -X -f scripts/neon-rehearsal/precheck.sql | tee "$OUT/precheck-after-restore.txt"
diff <(grep -v -i 'size\|kB\|MB\|bytes' "$OUT/precheck.txt") <(grep -v -i 'size\|kB\|MB\|bytes' "$OUT/precheck-after-restore.txt")
```

Pass: precheck section 4 shows no 2.0 objects again, and section 5 matches the first precheck. Record how long the restore took. Also write down the project's **history retention window** (Neon project settings). A production rollback is only possible within it, so the go/no-go decision after cutover must fall inside that window.

## Phase 11: record and clean up

1. Fill the results table below and keep `$OUT` (it holds row counts, not document content).
2. Delete both branches: `npx neonctl@latest branches delete "$BRANCH" --project-id "$NEON_PROJECT"`, and the same for `"$BRANCH-migrated"`.
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
| 1.0.1 build on the migrated branch: chat / reserve / rows in personal workspace; upload completion moves the file out of `uploading` with exactly 1 `ingestion_jobs` row (phase 9) | |
| restore to T0 works, seconds taken, retention window (phase 10) | |
| **Go / no-go** | |

**Go** = every row passes, or each deviation has a written decision.

## Phase 12: production cutover (separate approval, not part of the rehearsal)

Outline only. Each step touches production and needs its own OK.

1. **Before the window.** Give Preview its own database (`NEON_DATABASE_URL` for Preview → a non-production Neon branch), add `INTERNAL_SERVICE_SECRET` to Vercel (missing today), and run `docs/scribe2/T02-verification.md` on a preview. Identify the deployment built from the `main` commit you rehearsed. Production auto-assign stays paused. **Create the 1.0.1 production build without assigning the domain** (owner step, needs its own OK): from the 1.0.1 checkout (`../scribe-1.0.1`, branch `rollback/1.0-compat`) run `vercel deploy --prod --skip-domain` (`vercel deploy --help` lists `--skip-domain` as disabling the automatic promotion; `--prod` is shorthand for `--target=production`). Record its deployment URL next to the 2.0 one. It is a production-target build, so promoting it later uses the Production environment variables without a rebuild. It is harmless against the unmigrated production database, because a bare `ON CONFLICT DO NOTHING` works on the 1.0 schema, and it serves no traffic until promoted.
2. **Restore point.** Create a branch `pre-scribe2-<date>` from production immediately before migrating.
3. **Window.** Note `T0` (UTC) immediately before applying; `apply.sh` needs `ALLOW_PROD=1` here, and nowhere else. Avoid the crons (00:00 and 01:00 UTC = 05:30 and 06:30 IST). Run `precheck.sql` on production and compare it with the branch, run `apply.sh`, `postcheck.sql`, `--register-journal`, then **promote the 2.0 deployment immediately** (`vercel promote <deployment url>`). From the moment 0008 commits until 0020 commits, the running 1.0 build cannot create files, chats or RAG traces: its inserts omit the new NOT NULL `workspace_id`. That window is now only the time `apply.sh` takes to run 0008..0020 (one transaction per file, a few seconds in the local runs), no longer "until promote". Still promote promptly: 1.0 and 2.0 should not both serve for long. **If a migration file fails on production, do not debug live.** If it is 0008-0013 the 1.0 build stays broken, so roll back at once (step 5). If 0013 committed and the failure is in 0014-0019, 1.0 can be revived by applying 0020 alone (`psql -1 -f <(sed 's/^--> statement-breakpoint$//' drizzle/0020_legacy_workspace_default.sql)`), but treat that only as a way to stop the bleeding and still roll back or fix on a branch.
4. **After promote.** Run the TS legacy backfill, do the phase 8 smoke on production, and watch logs. Flip `INGEST_ENGINE=python` on a preview first, then production. Once it is `python`, code-only rollback is no longer clean (step 5, limit b).
5. **Rollback.** Two options. Use the first when 2.0 is only misbehaving, the second when a migration failed or data is damaged.
   - **Code-only (keeps data).** With 0020 applied, deploy `rollback/1.0-compat` (`baa1634`) instead of promoting `scribe-9wz4vw60w`: `vercel promote` the 1.0.1 deployment prepared in step 1 (a Vercel action for the owner). The 1.0.1 build inserts into the migrated schema (phase 9), and everything written under 2.0 stays. Limits: (a) 1.0 ignores `files.deleted_at`, so Sources soft-deleted under 2.0 reappear in 1.0 until they are purged; (b) Sources ingested by the Python engine (`INGEST_ENGINE=python`) have no `documents` rows, so 1.0 cannot search them; code-only rollback is clean only while 2.0 ran with `INGEST_ENGINE=legacy`; (c) promoting `scribe-9wz4vw60w` is only the fallback if no 1.0.1 build was prepared: chat, search, listing and deleting work, but every upload fails on `ON CONFLICT ("file_id")` (gap closed only in `rollback/1.0-compat`, `baa1634`); (d) 0020 must stay applied for as long as 1.0.1 is a rollback target. Drop it (`drizzle/rollback/0020_legacy_workspace_default.down.sql`, or a later migration) only after 1.0.1 is retired.
   - **Neon restore (loses data).** `vercel promote` the old deployment and restore the production branch to `T0` with the command rehearsed in phase 10 (the `pre-scribe2-<date>` branch is the fallback copy if `T0` is outside the retention window). Data written after `T0` is lost. Down files exist only for 0013-0020 (`drizzle/rollback/`); 0008-0012 have none, so for a failed or corrupting migration the Neon restore is the real rollback.

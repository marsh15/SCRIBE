# What only you can do, in order

Everything else is prepared in the repository. Each item has the exact command or click path and a success check. Do them in order; later items assume earlier ones. Nothing here was run by the assistant. Never paste a key, cookie or connection string into a chat, a commit or a log.

Legend: **[repo]** touches only this machine and git. **[PAID]** spends provider money (hard cap $10 total across everything; the eval commands keep a ledger). **[REMOTE]** touches Neon or Vercel.

## 0. Keep the machine healthy [repo]

OrbStack/Docker died once when the disk reached 100%; the test database vanishes with it. Keep at least 5 GB free (`df -h /System/Volumes/Data`). Start the database with `docker compose up -d db` (OrbStack: `open -a OrbStack` first).
Success: `docker ps` lists `scribe-db-1` as healthy.

## 1. Review and merge the pull requests [repo]

Open each PR listed in [FINISH_PLAN.md](FINISH_PLAN.md), read the description, wait for GitHub CI to be green, merge in the order listed there. PRs that add a migration are marked **HOLD**; none exists at the moment.
Success: `main` has all of them and CI on `main` is green.

## 2. Review the golden set [repo]

Follow [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md). Phase 1 (the 40 validation cases) is enough for the next steps; phases 2 and 3 can follow.

```bash
uv run --project intelligence scribe-eval review --items benchmarks/datasets/golden-v1/cases.jsonl --kind case --corpus fixtures/corpus --split validation,dev,test
```

Success: this prints `0 validation cases still unreviewed` (the one-liner is at the end of the checklist); after phases 2-3 `scribe-eval validate benchmarks/datasets/golden-v1 --require-reviewed` exits 0. Commit `cases.jsonl` (and `dropped.jsonl`) in a PR.

## 3. Provide the provider keys [repo]

```bash
umask 077
cat > /tmp/scribe-keys.env <<'EOF'
GOOGLE_GENERATIVE_AI_API_KEY=...
VOYAGE_API_KEY=...
EOF
chmod 600 /tmp/scribe-keys.env
scripts/arm-gate.sh --check
```

Add a payment method at Voyage to lift the default rate limit. Success: `--check` prints `preflight ok: keys file, reviewed 'validation' split, ... Nothing was run or spent.` Delete the file when you are finished (`rm /tmp/scribe-keys.env`).

## 4. Evaluate the lexical `or_rank` channel, then choose the default [PAID, small]

Why: with the original `and` lexical mode most natural-language questions return an empty lexical list, so "hybrid" was dense search. The `or_rank` profiles fix that structurally; whether they are better is not measured. Dev and validation only; never the test split.

```bash
docker compose up -d db
PGPASSWORD=scribe psql -h localhost -p 54329 -U scribe -d postgres -c 'CREATE DATABASE scribe_eval_test'
export DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/scribe_eval_test
scripts/apply-migrations.sh "$DATABASE_URL_TEST"
set -a; source /tmp/scribe-keys.env; set +a
uv run --project intelligence scribe-eval ingest-corpus --corpus fixtures/corpus --embedder gemini --database-url "$DATABASE_URL_TEST" --reset
for split in dev validation; do
  uv run --project intelligence scribe-eval run-profile --split $split --database-url "$DATABASE_URL_TEST" \
    --profiles dense-only,hybrid-rrf,hybrid-rrf-v2,hybrid-rrf-rerank,hybrid-rrf-rerank-v2 --out benchmarks/runs/rankings-$split
done
```

Score each ranking file with `scribe-eval score --split <split> --rankings ... --profile ...` and compare pairs with `scribe-eval compare` (full recipe: `docs/scribe2/benchmarks/final.md` section 2). Success: you have per-profile Recall@5/MRR with intervals on validation. Decide the default by the written rule in `m1-prelim.md` section 9, and record the decision in a PR. If `hybrid-rrf-rerank-v2` is not at least as good as `hybrid-rrf-rerank` on validation, switch the default back (`EVIDENCE_PROFILE` in `lib/python-retrieval.ts` and `DEFAULT_PROFILE` in `intelligence/src/scribe_eval/gate.py`) **before** step 5.

## 5. Arm the retrieval gate [PAID, once]

Needs steps 1-4. On a new branch:

```bash
git switch -c scribe2/arm-gate
scripts/arm-gate.sh
```

It refreshes the provider cache (the only paid step), writes the baseline with no keys in the environment, and runs the gate against itself. Then commit what it prints, open a PR, and in GitHub: Settings > Branches > `main` > require the `retrieval-gate` status check, and require review for `benchmarks/gate/**`.
Success: the script ends with `GATE PASSED`; CI on that PR shows `retrieval-gate` armed (no `GATE NOT ARMED` warning). The synthetic regression test already proves a worsened retrieval is rejected; after arming you can open a throwaway PR that reverses fusion and see CI go red.

## 6. Set the Vercel environment [REMOTE]

Vercel > Project > Settings > Environment Variables. Add for **Preview and Production**: `INTERNAL_SERVICE_SECRET` (32+ random bytes: `openssl rand -hex 32`), `VOYAGE_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`. Keep `INGEST_ENGINE` and `RETRIEVAL_ENGINE` unset (legacy) for Production; set both to `python` for Preview only. Success: the variables list shows them; no value is displayed or copied anywhere.

## 7. Back up and rehearse the migration on a Neon branch [REMOTE]

Follow [neon-rehearsal.md](neon-rehearsal.md) phases 0-11 in one shell. First note in the Neon console the project's history-retention window and confirm a recent backup/branch exists. Fill the results table in phase 11.
Success: every row of the table passes or has a written decision, the schema fingerprint diff is exactly the three known lines, and the restore to `T0` worked.

## 8. Give Preview its own database [REMOTE]

[runbook-promote-rollback.md](runbook-promote-rollback.md) step 1. **Until this is done, every preview, including PR previews, runs against the production database.**
Success: the Preview and Production `NEON_DATABASE_URL` hosts differ; the preview has the 2.0 schema while production still does not.

## 9. Preview smoke [REMOTE]

Runbook step 2: `npx tsx scripts/smoke.ts --base-url https://<preview> --lifecycle --attempts 40 --wait-ms 3000` with `SCRIBE_SESSION_TOKEN` set in your shell, plus the manual checks in `T02-verification.md`.
Success: all `PASS`, no `FAIL`; any `SKIP` is a decision you wrote down.

## 10. Promote [REMOTE]

Runbook steps 3-6, one at a time, stopping at each **STOP: verify**. Rollback is in the same file.
Success: production serves the 2.0 build, existing users see their chats and files, the second backfill run reports 0 files, postcheck passes.

## 11. Flip the engines [REMOTE]

Runbook step 7: `INGEST_ENGINE=python` on Preview, smoke again, then Production; `RETRIEVAL_ENGINE=python` after step 4's decision.
Success: the lifecycle smoke passes on Production; the daily sweep route answers 200.

## 12. Split the database credentials, then force RLS [REMOTE] (optional hardening)

Only after step 11 is stable. Today the app connects as the table owner, which bypasses row-level security. The TypeScript file, ingestion and chat paths and the Python IDOR/evidence paths are tested as a real non-owner login locally (`scripts/test-chat-rls.py`, `scripts/test-python-rls.py`), but **the whole Python service (ingest worker, compare, verify) as `scribe_app` against Neon is not exercised**. Do this on the rehearsal branch first:

1. `ALTER ROLE scribe_app LOGIN PASSWORD '<random>'`; build its pooled URL.
2. Preview: `NEON_DATABASE_URL` (runtime) = the `scribe_app` URL; `NEON_ADMIN_DATABASE_URL` = the owner URL; the Python service's `DATABASE_URL` = the `scribe_app` URL. Run the lifecycle smoke. The owner URL is used only by the allow-listed admin modules (`__tests__/security/db-boundary.test.ts`).
3. Only when green on the branch, repeat on Production, then run `drizzle/deferred/force_rls.sql`.
Success: smoke passes; `select rolbypassrls from pg_roles where rolname = current_user` is false for the runtime connection; the README row-level-security status can then be changed.

## 13. Run the user sessions [repo]

[user-study-protocol.md](user-study-protocol.md): consent, 8 tasks, notes spreadsheet. Success: 5-8 sessions with the record described there and a short write-up.

## 14. Record the demo [repo]

[demo-script.md](demo-script.md): one authenticated recording of the running deployment. Link it in the README under the existing illustration, with URL, commit and date. Success: you have watched it through and no token, cookie or private document is visible.

## 15. Produce the final report [PAID, once]

Only after steps 2 (all 200 reviewed), 4 and 5. `docs/scribe2/benchmarks/final.md` section 2 has the commands, including the single test-split run. Fill the report from `scribe-eval report` output only; delete the banner; then update README Status and `summary-bullets.md` with the measured numbers.
Success: no "NOT MEASURED" cell remains that you intend to claim; the ledger total is under $10.

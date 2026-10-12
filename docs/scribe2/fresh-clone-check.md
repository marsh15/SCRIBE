# Fresh-clone check (T33)

Goal: confirm that the commands in `README.md` work from a clean checkout, and record exactly what was executed and what was only read.

Date: 2026-10-12. Machine: the author's Mac. Free disk was 7.2 to 8.5 GB during the check, so a second copy of `node_modules` (1.1 GB) was not made.

## How the clone was made

1. `git clone --depth 1 file://<repo> /tmp/scribe-fresh` (committed state at `63fa5bf`, 15 MB). The clone contains only committed files. The T33 files were not committed yet, so these were copied over it with `rsync`: `README.md`, `docs/scribe2/{ARCHITECTURE,demo-script}.md`, `docs/scribe2/benchmarks/final.md`, `scripts/demo-seed/`, `intelligence/tests/evaluation/test_demo_seed.py`. `fresh-clone-check.md` and `summary-bullets.md` were not copied.
2. Node dependencies were **not installed** in the clone. Commands that need them used a **symlink** from `node_modules` to the original checkout's `node_modules` (no copy, no install). This shows that the scripts and configs in a clean tree work with a correct set of dependencies. It does **not** show that `npm ci` produces that set (see the dry run below).
3. Python dependencies were installed fresh in the clone with `uv sync` (109 MB venv).
4. A throwaway database `fresh_clone_test` was created in the existing local Postgres container (`scribe-db-bridge`, localhost:54329) and dropped afterwards. No Docker, network or OrbStack configuration was touched.
5. The clone and the database were deleted at the end.

## Executed (output seen)

| Command (in the clone) | Result |
|---|---|
| `npm ci --dry-run` | exit 0; resolved from `package-lock.json` (no packages installed; install-script warnings for msw, sharp, unrs-resolver) |
| `uv sync` (in `intelligence/`) | installed; venv 109 MB |
| `createdb ... fresh_clone_test` then `scripts/apply-migrations.sh postgresql://scribe:scribe@localhost:54329/fresh_clone_test` | schema built (35 tables in `public` afterwards) via `drizzle-kit export` plus migrations `0010`-`0018` |
| `uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1` | `OK`; totals: 80 dev, 40 validation, 80 test, 200 cases, 180 answerable |
| `uv run --project intelligence scribe-eval gate --status` | `gate armed: False (no baseline file at benchmarks/gate/baseline.json)` |
| `uv run --project intelligence scribe-eval ingest-corpus --corpus fixtures/corpus --embedder fake --database-url <fresh_clone_test> --reset` | all 24 documents ACTIVE, 187 chunks, FAKE embeddings (plumbing only) |
| `uv run --project intelligence python scripts/demo-seed/demo_seed.py ... --only glossary.txt --only runbook-deploy.md` | dry run: printed the plan, made no connection |
| same with `--apply --embedder fake`, run twice | both runs: 2 ready, 0 problems, same workspace id (idempotent) |
| `demo_seed.py --database-url postgresql://u:p@x.neon.tech/neondb ... --apply` | refused, exit 2, message names `--i-know` |
| `uv run pytest -q tests/evaluation/test_demo_seed.py tests/schema tests/test_schema_contract.py tests/test_auth.py` with `DATABASE_URL_TEST` set to the clone database | 70 passed |
| `uv run --project intelligence scribe-perf --help`; `uv run python -m scribe_ingest.upgrade --help` | usage printed |
| `npx tsc --noEmit` (symlinked node_modules) | no errors |
| `npx vitest run` (symlinked node_modules) | 45 files passed, 2 skipped; 403 tests passed, 3 skipped |
| `npm run lint` (symlinked node_modules) | no output other than the script banner |
| `docker compose config -q` | valid |
| `npx tsx scripts/backfill-legacy-generations.ts --help` | `tsx` is not a declared dependency, so `npx` downloaded it; the script rejects `--help` ("unknown argument"). Its real flags are `--batch-size`, `--after-id`, `--dry-run`, `--psql` |

## Not executed (read or help-checked only)

- `npm ci` (real install), `npm run dev`, `npm run build`, `npm run check` (build needs Clerk keys; CI sets placeholders).
- `docker compose up -d db` was not run in the clone. The existing container served as the database, and the compose file was only validated with `docker compose config -q`.
- `npx drizzle-kit migrate` against an existing database (README says to try it on a Neon branch first).
- Anything that calls Gemini, Voyage, Vercel, Neon or Clerk. The real-embedder forms of `ingest-corpus` and `demo_seed.py --apply` (without `--embedder fake`) were not run. Their dry-run and refusal paths were.
- `scripts/eval-baseline.ts` (needs Node 18, a key and a database), `scribe-eval score/answer/compare/publish-run/judge-*` (need a rankings file or a run), `scribe-eval gate` runs (need a cache and baseline that do not exist), `scribe-perf run`.
- The full Python suite was not run inside the clone. It was run in the working checkout (see the T33 report).

## README fixes made because of this check

- `npx tsx` downloads `tsx` on first use because it is not in `package.json`. The README now notes this next to the backfill command.
- `python -m scribe_ingest.upgrade` must be run from `intelligence/` through `uv run`; README now says so.

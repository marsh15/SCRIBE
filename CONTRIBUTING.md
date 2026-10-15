# Contributing

Setup and commands are in the [README](README.md#getting-started). This file lists the rules that are easy to break and that CI or review will catch.

## Before you open a pull request

Run what CI runs:

```bash
npm run check                                   # lint, typecheck, vitest, next build (needs Clerk keys; CI uses placeholders)
cd intelligence
DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/<db with "test" in its name> uv run pytest -q
```

Database-backed Python tests are skipped without `DATABASE_URL_TEST`, and a skipped test proves nothing, so run them with a database when you touch SQL, ingestion or retrieval. Build that database with `scripts/apply-migrations.sh <url>` (it drops and recreates schema `public`; use a disposable database).

## Database changes

- Edit `lib/db-schema.ts` for tables and columns. Put anything Drizzle cannot express (triggers, RLS, roles, functions, HNSW and GIN indexes) in a new hand-written, idempotent SQL file in `drizzle/` and add it to `drizzle/meta/_journal.json`.
- Add a matching `drizzle/rollback/NNNN_*.down.sql`.
- Never run `drizzle-kit push` against a database with the RLS objects. Use `drizzle-kit generate` plus hand-written SQL.
- Profile rows (`embedding_profiles`, `chunk_profiles`, `index_profiles`, `retrieval_profiles`) are immutable. A changed setting is a new row with a new name, not an edit.
- `scripts/test-migrations-nosuperuser.sh` must keep passing: migrations run on Neon as a role that is not a superuser.
- Migrations `0000`-`0009` are not replayable on an empty database. Do not rely on replaying the full history.

## Routes and access control

Every `app/api/**/route.ts` file and every server action must have a row in `__tests__/security/route-table.ts`; `route-guard.test.ts` fails otherwise. Choose the category carefully (`idor`, `internal`, `session`, `gone`, `public`): the IDOR sweep then tests it. A foreign id must be indistinguishable from an unknown one (404).

The Python service takes the workspace only from the verified internal token or the job row. Do not add a route that reads a workspace id from a request body.

## Python dependencies

Runtime dependencies are listed twice: in `intelligence/pyproject.toml` and in the root `requirements.txt`, which the Vercel Python runtime uses. Keep them in sync by hand; `intelligence/tests/test_requirements_sync.py` fails when they differ.

## Evaluation rules

- Do not tune against the `test` split. The CLI refuses it without `--milestone-report`.
- Do not commit results produced with fake embeddings or a fake reranker, and do not present them as measurements.
- Every result file records the dataset version, profile hash, commit and model ids. Report regressions as well as improvements.
- Changes to chunking, document text, case questions or the rerank input invalidate the gate's cached provider responses; see `benchmarks/gate/README.md`.

## Documentation

- Describe what the code does today. Mark anything not run against a real deployment or provider as not verified.
- If code and `docs/scribe2/DESIGN.md` disagree, fix one of them in the same change.
- Regenerate the architecture diagram with `python3 scripts/gen_architecture_diagram.py` instead of editing the SVG; update the script's tables when a component changes.
- Decisions that are expensive to reverse get an ADR in `docs/adr/`.

## Secrets

Never commit keys, tokens, connection strings or `.env*` files (`.gitignore` covers `.env`, `.env.local` and `.env.*.local`). `.env.example` holds names only.

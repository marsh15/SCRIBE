#!/usr/bin/env bash
# Build the test DB schema from lib/db-schema.ts via `drizzle-kit export` (no DB
# connection needed). The committed migration history is not replayable from an
# empty database (0004 assumes pre-existing columns), so we materialise the
# current schema instead. Drizzle stays the only schema owner.
set -euo pipefail
url="${1:-${DATABASE_URL_TEST:?set DATABASE_URL_TEST or pass a URL}}"
PGOPTIONS="-c client_min_messages=warning" psql "$url" -v ON_ERROR_STOP=1 -q -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public; CREATE EXTENSION IF NOT EXISTS vector;'
NEON_DATABASE_URL=postgresql://unused npx drizzle-kit export --sql | grep -v "^\[dotenv" | psql "$url" -v ON_ERROR_STOP=1 -q

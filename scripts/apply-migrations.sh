#!/usr/bin/env bash
# Build the test DB schema from lib/db-schema.ts via `drizzle-kit export` (no DB
# connection needed). The committed migration history is not replayable from an
# empty database (0004 assumes pre-existing columns), so we materialise the
# current schema instead. Drizzle stays the only schema owner.
set -euo pipefail
url="${1:-${DATABASE_URL_TEST:?set DATABASE_URL_TEST or pass a URL}}"
PGOPTIONS="-c client_min_messages=warning" psql "$url" -v ON_ERROR_STOP=1 -q -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public; CREATE EXTENSION IF NOT EXISTS vector;'
NEON_DATABASE_URL=postgresql://unused npx drizzle-kit export --sql | grep -v "^\[dotenv" | psql "$url" -v ON_ERROR_STOP=1 -q

# The export above only knows tables/columns/indexes. Everything else (HNSW/GIN indexes, immutability triggers,
# seed profiles, RLS policies, roles, SECURITY DEFINER functions) lives in hand-written, idempotent migrations
# 0010+, so replay those in order on top. (0000-0009 are NOT replayable from empty; the export covers them.)
# Not journaled/deferred SQL (drizzle/deferred, drizzle/rollback) is in subdirectories and is not matched.
root="$(cd "$(dirname "$0")/.." && pwd)"
for f in "$root"/drizzle/00[1-9][0-9]_*.sql; do
  [ -e "$f" ] || continue
  PGOPTIONS="-c client_min_messages=warning" psql "$url" -v ON_ERROR_STOP=1 -q -f <(sed 's/^--> statement-breakpoint$//' "$f")
done

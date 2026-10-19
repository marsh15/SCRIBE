#!/usr/bin/env bash
# Apply the Scribe 2.0 migrations (every drizzle/00NN_*.sql with NN >= 08) to a NEON BRANCH, one transaction per file,
# timed, the way scripts/apply-migrations.sh replays them in tests. Unlike that script it never drops anything.
# Runbook: docs/scribe2/neon-rehearsal.md. Do not point this at production until a rehearsal has passed.
#
#   PROD_HOST=<production endpoint host> CONFIRM_HOST=<host of the URL> scripts/neon-rehearsal/apply.sh <direct postgres url>
#   CONFIRM_HOST=<host of the URL> scripts/neon-rehearsal/apply.sh <direct postgres url> --register-journal
#
# --register-journal applies nothing. It records the same files in drizzle.__drizzle_migrations (hash = sha256 of the
# file, created_at = the journal "when", exactly what drizzle-kit migrate writes) so a later `drizzle-kit migrate`
# does not try to replay them.
set -euo pipefail
url="${1:?usage: CONFIRM_HOST=<host> $0 <direct postgres url> [--register-journal]}"
mode="${2:-}"
host="$(printf '%s' "$url" | sed -E 's#^[a-z]+://([^@]*@)?([^/:?]+).*#\2#')"
[ "${CONFIRM_HOST:-}" = "$host" ] || { echo "refusing: set CONFIRM_HOST=$host to confirm this target" >&2; exit 2; }
case "$host" in *-pooler.*) echo "refusing: use the direct (non -pooler) endpoint for migrations" >&2; exit 2 ;; esac
# PROD_HOST (the production endpoint host, pooler or direct) makes production an explicit opt-in: ALLOW_PROD=1.
if [ -n "${PROD_HOST:-}" ] && [ "$host" = "${PROD_HOST/-pooler./.}" ] && [ "${ALLOW_PROD:-}" != 1 ]; then
  echo "refusing: $host is PROD_HOST; set ALLOW_PROD=1 only for the approved cutover" >&2; exit 2
fi

root="$(cd "$(dirname "$0")/../.." && pwd)"
files=()
for f in "$root"/drizzle/00[0-9][0-9]_*.sql; do
  n="$(basename "$f")"; (( 10#${n:0:4} >= 8 )) && files+=("$f")
done
psql_q() { PGOPTIONS="-c client_min_messages=warning" psql "$url" -X -v ON_ERROR_STOP=1 -q "$@"; }

if [ "$mode" = "--register-journal" ]; then
  sql="CREATE SCHEMA IF NOT EXISTS drizzle;
CREATE TABLE IF NOT EXISTS drizzle.__drizzle_migrations (id SERIAL PRIMARY KEY, hash text NOT NULL, created_at bigint);"
  for f in "${files[@]}"; do
    tag="$(basename "$f" .sql)"
    when="$(node -e 'const j=require(process.argv[1]);const e=j.entries.find(e=>e.tag===process.argv[2]);if(!e)process.exit(1);console.log(e.when)' "$root/drizzle/meta/_journal.json" "$tag")" \
      || { echo "$tag is not in drizzle/meta/_journal.json" >&2; exit 1; }
    hash="$(shasum -a 256 "$f" | cut -d' ' -f1)"
    sql+="
INSERT INTO drizzle.__drizzle_migrations (hash, created_at) SELECT '$hash', $when
  WHERE NOT EXISTS (SELECT 1 FROM drizzle.__drizzle_migrations WHERE created_at = $when);"
  done
  psql_q -1 -c "$sql"
  psql_q -c "SELECT id, hash, created_at FROM drizzle.__drizzle_migrations ORDER BY created_at"
  exit 0
fi
[ -z "$mode" ] || { echo "unknown option: $mode" >&2; exit 2; }

total_start=$SECONDS
for f in "${files[@]}"; do
  start=$SECONDS
  psql_q -1 -f <(sed 's/^--> statement-breakpoint$//' "$f")
  echo "$(basename "$f")  ok  $((SECONDS - start))s"
done
echo "all ${#files[@]} files applied in $((SECONDS - total_start))s"

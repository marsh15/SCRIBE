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
host="$(node -e 'try {
  const u = new URL(process.argv[1]);
  if (!["postgres:", "postgresql:"].includes(u.protocol) || !u.hostname) throw new Error();
  process.stdout.write(u.hostname.replace(/^\[|\]$/g, ""));
} catch { console.error("refusing: invalid PostgreSQL URL"); process.exit(2); }' "$url")"
[ "${CONFIRM_HOST:-}" = "$host" ] || { echo "refusing: set CONFIRM_HOST=$host to confirm this target" >&2; exit 2; }
case "$host" in *-pooler.*) echo "refusing: use the direct (non -pooler) endpoint for migrations" >&2; exit 2 ;; esac
# PROD_HOST (the production endpoint host, pooler or direct) makes production an explicit opt-in: ALLOW_PROD=1.
if [ -n "${PROD_HOST:-}" ] && [ "$host" = "${PROD_HOST/-pooler./.}" ] && [ "${ALLOW_PROD:-}" != 1 ]; then
  echo "refusing: $host is PROD_HOST; set ALLOW_PROD=1 only for the approved cutover" >&2; exit 2
fi

root="$(cd "$(dirname "$0")/../.." && pwd)"
# Capture the manifest before connecting: missing/unordered SQL or corrupt journal metadata must never partially apply.
manifest="$(node "$root/scripts/neon-rehearsal/check-journal.mjs" "$root")"
[ -n "$manifest" ] || { echo "refusing: migration manifest is empty" >&2; exit 1; }
files=()
while IFS=$'\t' read -r tag when hash; do
  files+=("$root/drizzle/$tag.sql")
done <<< "$manifest"
psql_q() { PGOPTIONS="-c client_min_messages=warning" psql "$url" -X -v ON_ERROR_STOP=1 -q "$@"; }

if [ "$mode" = "--register-journal" ]; then
  times=""
  first_when=""
  while IFS=$'\t' read -r tag when hash; do
    first_when="${first_when:-$when}"
    times+="${times:+,}$when"
  done <<< "$manifest"
  sql="SELECT pg_advisory_xact_lock(hashtext('scribe:drizzle:journal'));
CREATE SCHEMA IF NOT EXISTS drizzle;
CREATE TABLE IF NOT EXISTS drizzle.__drizzle_migrations (id SERIAL PRIMARY KEY, hash text NOT NULL, created_at bigint);
DO \$journal\$ BEGIN
  IF EXISTS (SELECT 1 FROM drizzle.__drizzle_migrations
             WHERE created_at IS NULL OR (created_at >= $first_when AND created_at NOT IN ($times))) THEN
    RAISE EXCEPTION 'migration journal mismatch: unexpected timestamp; investigate before registering';
  END IF;
END \$journal\$;"
  while IFS=$'\t' read -r tag when hash; do
    sql+="
DO \$journal\$ BEGIN
  IF EXISTS (SELECT 1 FROM drizzle.__drizzle_migrations
             WHERE (created_at = $when AND hash IS DISTINCT FROM '$hash')
                OR (hash = '$hash' AND created_at IS DISTINCT FROM $when))
     OR (SELECT count(*) FROM drizzle.__drizzle_migrations WHERE created_at = $when) > 1 THEN
    RAISE EXCEPTION 'migration journal mismatch for $tag; investigate before registering';
  END IF;
END \$journal\$;
INSERT INTO drizzle.__drizzle_migrations (hash, created_at) SELECT '$hash', $when
  WHERE NOT EXISTS (SELECT 1 FROM drizzle.__drizzle_migrations WHERE created_at = $when);"
  done <<< "$manifest"
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

#!/usr/bin/env bash
# P0-1: prove migrations 0008+ apply (and re-apply) as a NOSUPERUSER CREATEROLE login role that owns the database,
# which is what a managed Postgres (Neon) gives you. Nothing here can touch a real database: it creates a throwaway
# database named scribe_nosuper_test and a role named scribe_migr_test, and drops the database again at the end.
#
#   scripts/test-migrations-nosuperuser.sh [ADMIN_URL]     (default $DATABASE_URL_TEST; must be a SUPERUSER url)
#
# Steps: schema from lib/db-schema.ts (drizzle-kit export) as the unprivileged role, then every hand-written migration
# 0010+ in order, then 0010+ AGAIN (re-runs must work), then assertions on function ownership and grants.
set -euo pipefail
admin="${1:-${DATABASE_URL_TEST:?set DATABASE_URL_TEST or pass a superuser URL}}"
base="${admin%/*}"
db="scribe_nosuper_test"
role="scribe_migr_test"
case "$db" in *test*) ;; *) echo "refusing: database name must contain 'test'" >&2; exit 1 ;; esac
migr="$(echo "$base" | sed -E "s#//[^@]*@#//$role:migr@#")/$db"
root="$(cd "$(dirname "$0")/.." && pwd)"
export PGOPTIONS="-c client_min_messages=warning"
adm() { psql "$admin" -v ON_ERROR_STOP=1 -q -Atc "$1"; }

adm "select 1" >/dev/null
[ "$(adm "select rolsuper from pg_roles where rolname = current_user")" = "t" ] || { echo "ADMIN_URL must be a superuser" >&2; exit 1; }
echo "server: $(adm 'show server_version')"

adm "drop database if exists $db with (force)"
if [ "$(adm "select count(*) from pg_roles where rolname = '$role'")" = "0" ]; then
  adm "create role $role login nosuperuser createrole nocreatedb nobypassrls password 'migr'"
fi
# scribe_app / scribe_definer are cluster-wide. On a fresh server the migrating role creates them (and, on PG16+,
# automatically gets ADMIN OPTION on them, without SET/INHERIT). If an earlier run created them as superuser, give the
# role that same ADMIN-only membership so the test models the fresh case.
for r in scribe_app scribe_definer; do
  if [ "$(adm "select count(*) from pg_roles where rolname = '$r'")" != "0" ] && [ "$(adm 'show server_version_num')" -ge 160000 ]; then
    adm "grant $r to $role with admin option, inherit false, set false"
  fi
done
adm "create database $db owner $role"
trap 'adm "drop database if exists '"$db"' with (force)" >/dev/null 2>&1 || true; adm "drop role if exists '"$role"'" >/dev/null 2>&1 || true' EXIT
# Managed Postgres pre-approves pgvector for database owners; the Docker image does not, so install it as the superuser.
psql "${base}/$db" -v ON_ERROR_STOP=1 -q -c "CREATE EXTENSION IF NOT EXISTS vector"

NEON_DATABASE_URL=postgresql://unused npx drizzle-kit export --sql | grep -v "^\[dotenv" | psql "$migr" -v ON_ERROR_STOP=1 -q
apply() {
  for f in "$root"/drizzle/00[1-9][0-9]_*.sql; do
    [ -e "$f" ] || continue
    psql "$migr" -v ON_ERROR_STOP=1 -q -f <(sed 's/^--> statement-breakpoint$//' "$f") || { echo "FAILED: $f ($1)" >&2; return 1; }
  done
}
echo "== first application as $role"; apply first
echo "== second application (idempotent re-run)"; apply second

echo "== assertions"
who="$(psql "$migr" -v ON_ERROR_STOP=1 -Atc "select 'rolsuper=' || rolsuper::text || ' createrole=' || rolcreaterole::text from pg_roles where rolname = current_user")"
out="$(psql "$migr" -v ON_ERROR_STOP=1 -Atc "
  select proname || ' owner=' || pg_get_userbyid(proowner) from pg_proc
   where pronamespace = 'public'::regnamespace and (proname like 'app\_%' or proname like 'scribe\_%') and prosecdef
   order by 1")"
echo "$who"; echo "$out"
echo "$who" | grep -q "rolsuper=false" || { echo "migrating role is a superuser: test is meaningless" >&2; exit 1; }
[ -n "$out" ] || { echo "no SECURITY DEFINER functions found" >&2; exit 1; }
bad="$(echo "$out" | grep ' owner=' | grep -v 'owner=scribe_definer' || true)"
[ -z "$bad" ] || { echo "SECURITY DEFINER functions not owned by scribe_definer:" >&2; echo "$bad" >&2; exit 1; }
psql "$migr" -v ON_ERROR_STOP=1 -Atc "select has_function_privilege('scribe_app', 'public.app_claim_ingestion_job(integer,integer)', 'execute')" | grep -q '^t$' \
  || { echo "scribe_app cannot execute app_claim_ingestion_job" >&2; exit 1; }
psql "$migr" -v ON_ERROR_STOP=1 -Atc "select has_schema_privilege('scribe_definer', 'public', 'create')" | grep -q '^f$' \
  || { echo "scribe_definer still has CREATE on schema public" >&2; exit 1; }
[ "$(psql "$migr" -v ON_ERROR_STOP=1 -Atc "select count(*) from pg_constraint where contype = 'f' and conname like '%\_ws\_fk'")" -ge 20 ] \
  || { echo "0019 composite workspace FKs missing" >&2; exit 1; }
echo "OK: migrations apply and re-apply as a NOSUPERUSER CREATEROLE role"

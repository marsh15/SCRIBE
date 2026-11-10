#!/usr/bin/env bash
# Arm the retrieval regression gate (T31) in ONE command, run by the owner, locally, once.
#
#   scripts/arm-gate.sh --check     preflight only: keys, review state, disk. No Docker, no database, no provider call.
#   scripts/arm-gate.sh             preflight, then refresh the cache (PAID provider calls), write the baseline, run the gate.
#
# It never commits and never pushes. It prints the git commands at the end for you to review and run.
# Spec: benchmarks/gate/README.md and docs/scribe2/release-rules.md. Exit codes: 2 refused (CI or main), 3 keys,
# 4 unreviewed validation cases, 5 disk, 6 tooling missing, 7 the gate itself failed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEYS_FILE="${ARM_GATE_KEYS_FILE:-/tmp/scribe-keys.env}"
DATASET="${ARM_GATE_DATASET:-$ROOT/benchmarks/datasets/golden-v1}"
MIN_FREE_MB="${ARM_GATE_MIN_FREE_MB:-2048}"
DB_NAME="${ARM_GATE_DB:-scribe_gate_test}"
SPLIT="validation"
CHECK_ONLY=0
case "${1:-}" in
  "") ;;
  --check) CHECK_ONLY=1 ;;
  *) echo "usage: $0 [--check]   (anything else is refused: the default run spends provider money)" >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo "usage: $0 [--check]" >&2; exit 2; }

die() { local code="$1"; shift; echo "$*" >&2; exit "$code"; }

[[ -n "${CI:-}" ]] && die 2 "refusing: this script spends provider money and must never run in CI (CI is set)"

# 1. keys: a file of KEY=value lines, mode 600, never printed, loaded only for the refresh step
[[ -f "$KEYS_FILE" ]] || die 3 "BLOCKED: no keys file at $KEYS_FILE. Create it (chmod 600) with GOOGLE_GENERATIVE_AI_API_KEY=... and VOYAGE_API_KEY=... lines."
mode="$(stat -c '%a' "$KEYS_FILE" 2>/dev/null || stat -f '%Lp' "$KEYS_FILE")"   # GNU first: BSD stat -f means something else on Linux
[[ "$mode" == "600" ]] || die 3 "BLOCKED: $KEYS_FILE has mode $mode; run: chmod 600 $KEYS_FILE"
# Load in a subshell and test the VALUES (quotes and duplicate lines count): nothing is printed, nothing leaks out.
for var in GOOGLE_GENERATIVE_AI_API_KEY VOYAGE_API_KEY; do
  ( set -a; source "$KEYS_FILE" 2>/dev/null; [[ -n "${!var:-}" ]] ) || die 3 "BLOCKED: $var is missing or empty in $KEYS_FILE"
done

# 2. every case of the gated split must be human-reviewed (the baseline step refuses otherwise; fail early instead)
python3 - "$DATASET/cases.jsonl" "$SPLIT" <<'PY' && rc=0 || rc=$?
import json, sys
path, split = sys.argv[1:3]
rows = [json.loads(l) for l in open(path) if l.strip()]
todo = [r["id"] for r in rows if r.get("split") == split and r.get("reviewed") is not True]
total = sum(1 for r in rows if r.get("split") == split)
if todo:
    print(f"BLOCKED: {len(todo)} of {total} '{split}' cases are not reviewed (first: {', '.join(todo[:5])}).\n"
          f"Review them: uv run --project intelligence scribe-eval review --items {path} --corpus fixtures/corpus --split {split}\n"
          f"Checklist: docs/scribe2/REVIEW_CHECKLIST.md", file=sys.stderr)
    sys.exit(4)
PY
case "$rc" in 0) ;; 4) exit 4 ;; *) die 6 "could not read the dataset at $DATASET/cases.jsonl (python3 exit $rc)" ;; esac

# 3. disk: Docker dies when the disk fills up
free_mb="$(df -Pk "$ROOT" | awk 'NR==2 {print int($4/1024)}')"
(( free_mb >= MIN_FREE_MB )) || die 5 "BLOCKED: only ${free_mb} MB free disk (need $MIN_FREE_MB). Free space first; Docker stops when the disk is full."

if (( CHECK_ONLY )); then
  echo "preflight ok: keys file, reviewed '$SPLIT' split, ${free_mb} MB free. Nothing was run or spent."
  exit 0
fi

# 4. tooling and branch
for tool in docker psql uv npx git; do command -v "$tool" >/dev/null || die 6 "missing tool: $tool"; done
docker info >/dev/null 2>&1 || die 6 "Docker is not running. Start OrbStack/Docker, then re-run."
branch="$(git -C "$ROOT" branch --show-current)"
[[ "$branch" != "main" ]] || die 2 "refusing to run on main: create a branch first (git switch -c scribe2/arm-gate)"

# 5. throwaway database (name contains 'test'); dropped on exit
(cd "$ROOT" && docker compose up -d db >/dev/null)
export PGPASSWORD=scribe
admin_url="postgresql://scribe:scribe@localhost:54329/postgres"
for _ in $(seq 1 30); do psql "$admin_url" -Atc 'select 1' >/dev/null 2>&1 && break; sleep 2; done
psql "$admin_url" -Atc 'select 1' >/dev/null 2>&1 || die 6 "no PostgreSQL answered on localhost:54329 with user scribe within 60 s (is another Postgres on that port?)"
[[ "$DB_NAME" == *test* ]] || die 2 "database name must contain 'test'"
psql "$admin_url" -qc "DROP DATABASE IF EXISTS $DB_NAME WITH (FORCE)" -c "CREATE DATABASE $DB_NAME"
trap 'psql "$admin_url" -qc "DROP DATABASE IF EXISTS '"$DB_NAME"' WITH (FORCE)" >/dev/null 2>&1 || true' EXIT
DB_URL="postgresql://scribe:scribe@localhost:54329/$DB_NAME"
(cd "$ROOT" && scripts/apply-migrations.sh "$DB_URL" >/dev/null)

cli() { (cd "$ROOT" && uv run --project intelligence scribe-eval "$@" --dataset "$DATASET"); }

# 6. refresh the cache: the only step that calls Gemini and Voyage. Keys exist in this subshell only.
echo ">> refreshing the provider cache (paid calls: corpus embeddings, $SPLIT queries, rerank)"
( set -a; source "$KEYS_FILE"; set +a; cli gate --refresh-cache --database-url "$DB_URL" )

# 7. everything after this runs with no keys at all
unset GOOGLE_GENERATIVE_AI_API_KEY VOYAGE_API_KEY
echo ">> writing the baseline from the cache"
cli gate --write-baseline --database-url "$DB_URL"
echo ">> the gate must pass against its own baseline"
cli gate --database-url "$DB_URL" || die 7 "the gate failed against its own baseline; do not commit these files"

cat <<MSG

Gate files written. Review them, then commit them yourself:

  git -C $ROOT diff --stat benchmarks/gate/
  git -C $ROOT add benchmarks/gate/cache.jsonl.gz benchmarks/gate/baseline.json
  git -C $ROOT commit -m "arm the retrieval gate on the reviewed validation split"

Then in GitHub: Settings > Branches > main > require the 'retrieval-gate' status check, and require a review for
changes under benchmarks/gate/. A baseline in a PR is a change to the acceptance bar.
MSG

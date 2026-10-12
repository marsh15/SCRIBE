import { sql } from "@/lib/db-config";
import {
  normalizeResult,
  normalizeRun,
  UUID_RE,
  type EvalCaseResult,
  type EvalRun,
  type RawResultRow,
  type RawRunRow,
} from "@/lib/eval/model";

/**
 * Read side of the eval dashboard (T30).
 *
 * ACCESS RULE (documented in docs/scribe2/T30-notes.md): the eval tables are global (no workspace column) and describe
 * runs against the corpus held in the 'eval' workspace. A signed-in user may see them only if they own a workspace of
 * kind 'eval' (`workspaces.owner_user_id`) or hold a `workspace_members` row in one. Everyone else gets a 404 (the page
 * and the nav link behave as if the feature did not exist). Only rows whose stored `meta.workspace_kind` is 'eval' are
 * ever returned, so rows written by anything else into the same tables stay invisible.
 *
 * The SQL in each function is also executed by intelligence/tests/integration/test_eval_dashboard.py, which extracts the
 * first sql`...` after the function name: keep placeholders as plain `${name}` identifiers.
 */

type Row = Record<string, unknown>;
const MAX_RUNS = 200;

export async function canViewEval(userId: string): Promise<boolean> {
  if (!userId) return false;
  const rows = (await sql`
    SELECT 1 AS ok
    FROM "workspaces" w
    WHERE w."kind" = 'eval'
      AND (w."owner_user_id" = ${userId}
           OR EXISTS (SELECT 1 FROM "workspace_members" m WHERE m."workspace_id" = w."id" AND m."user_id" = ${userId}))
    LIMIT 1
  `) as Row[];
  return rows.length > 0;
}

export async function listEvalRuns(maxRuns: number = MAX_RUNS): Promise<EvalRun[]> {
  const limit = Math.min(Math.max(Math.trunc(maxRuns) || MAX_RUNS, 1), MAX_RUNS);
  const rows = (await sql`
    SELECT r."id", r."dataset_version", r."split", r."profile_id", r."config_hash", r."code_commit", r."meta",
           r."started_at", r."finished_at",
           (SELECT count(*) FROM "eval_results" x WHERE x."run_id" = r."id") AS case_count
    FROM "eval_runs" r
    WHERE r."meta"->>'workspace_kind' = 'eval'
    ORDER BY r."started_at" DESC, r."id"
    LIMIT ${limit}::int
  `) as unknown as RawRunRow[];
  return rows.map(normalizeRun);
}

export async function getEvalRun(runId: string): Promise<EvalRun | null> {
  if (!UUID_RE.test(runId)) return null;
  const rows = (await sql`
    SELECT r."id", r."dataset_version", r."split", r."profile_id", r."config_hash", r."code_commit", r."meta",
           r."started_at", r."finished_at",
           (SELECT count(*) FROM "eval_results" x WHERE x."run_id" = r."id") AS case_count
    FROM "eval_runs" r
    WHERE r."id" = ${runId}::uuid AND r."meta"->>'workspace_kind' = 'eval'
  `) as unknown as RawRunRow[];
  return rows[0] ? normalizeRun(rows[0]) : null;
}

export async function listEvalResults(runId: string): Promise<EvalCaseResult[]> {
  if (!UUID_RE.test(runId)) return [];
  const rows = (await sql`
    SELECT x."case_id", x."metrics", x."ranking", x."latency_ms", x."cost"
    FROM "eval_results" x
    JOIN "eval_runs" r ON r."id" = x."run_id"
    WHERE x."run_id" = ${runId}::uuid AND r."meta"->>'workspace_kind' = 'eval'
    ORDER BY x."case_id"
  `) as unknown as RawResultRow[];
  return rows.map(normalizeResult);
}

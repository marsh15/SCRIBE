import { after, NextResponse } from "next/server";
import { isAdmin } from "@/lib/auth";
import { sql } from "@/lib/db-config";
import { env } from "@/lib/env";
import { fireTick } from "@/lib/ingestion/tick";
import { requeueFailedBackfillJob } from "@/lib/ingestion/python-queue";
import { progressPercent } from "@/app/admin/migration/progress";

export const dynamic = "force-dynamic";

const SCAN_SQL = `
  SELECT f.id AS source_id, f.name, f.active_version_id, f.desired_version_id,
    f.storage_url, f.reupload_required_at,
    j.id AS job_id, j.status AS job_status, j.stage AS job_stage,
    j.error_code, j.last_error,
    g.id AS generation_id, COALESCE(g.embedded_chunks, 0) AS embedded_chunks,
    COALESCE(g.expected_chunks, 0) AS expected_chunks,
    CASE
      WHEN g.status IN ('ACTIVE', 'READY') THEN 'upgraded'
      WHEN f.desired_version_id IS DISTINCT FROM f.active_version_id THEN 'newer_upload'
      WHEN j.status IN ('queued', 'processing', 'retrying') THEN 'building'
      WHEN f.reupload_required_at IS NOT NULL THEN 'reupload'
      WHEN j.status = 'failed' THEN 'failed'
      WHEN j.id IS NOT NULL THEN 'stalled'
      WHEN COALESCE(BTRIM(f.storage_url), '') = '' THEN 'no_blob'
      ELSE 'pending'
    END AS state
  FROM files f
  JOIN workspaces w ON w.id = f.workspace_id
  LEFT JOIN LATERAL (
    SELECT generation.id, generation.status, generation.embedded_chunks, generation.expected_chunks
    FROM index_generations generation
    JOIN index_profiles profile ON profile.id = generation.index_profile_id
    WHERE generation.source_version_id = f.active_version_id
      AND generation.source_id = f.id
      AND generation.workspace_id = f.workspace_id
      AND profile.name = 'structural-v1'
    LIMIT 1
  ) g ON TRUE
  LEFT JOIN LATERAL (
    SELECT job.id, job.status, job.stage, job.error_code, job.last_error
    FROM ingestion_jobs job
    WHERE job.file_id = f.id AND job.source_version_id = f.active_version_id
      AND job.generation_id = g.id
    ORDER BY job.id DESC LIMIT 1
  ) j ON TRUE
  WHERE f.deleted_at IS NULL AND f.active_version_id IS NOT NULL AND w.kind <> 'eval'
  ORDER BY f.id
`;

interface ProgressRow {
  source_id: number;
  name: string;
  active_version_id: string;
  desired_version_id: string | null;
  storage_url: string | null;
  reupload_required_at: Date | string | null;
  job_id: number | null;
  job_status: string | null;
  job_stage: string | null;
  error_code: string | null;
  last_error: string | null;
  generation_id: string | null;
  embedded_chunks: number;
  expected_chunks: number;
  state: string;
}

async function migrationProgress() {
  const rows = await sql.query(SCAN_SQL) as unknown as ProgressRow[];
  const failures = rows
    .filter((row) => row.state === "failed" || row.error_code)
    .map((row) => ({
      sourceId: row.source_id,
      name: row.name,
      stage: row.job_stage ?? "FAILED",
      errorCode: row.error_code,
      lastError: row.last_error,
    }));
  const expectedChunks = rows.reduce((total, row) => total + row.expected_chunks, 0);
  const embeddedChunks = rows.reduce((total, row) => total + row.embedded_chunks, 0);
  const total = rows.length;

  return {
    profile: "structural-v1",
    sources: rows.map((row) => ({
      sourceId: row.source_id,
      name: row.name,
      state: row.state,
      stage: row.job_stage ?? (row.state === "upgraded" ? "DONE" : row.state.toUpperCase()),
      generationId: row.generation_id,
      embeddedChunks: row.embedded_chunks,
      expectedChunks: row.expected_chunks,
      progressPct: progressPercent(row.embedded_chunks, row.expected_chunks),
      jobId: row.job_id,
      jobStatus: row.job_status,
      errorCode: row.error_code,
    })),
    totals: {
      sources: total,
      embeddedChunks,
      expectedChunks,
      progressPct: progressPercent(embeddedChunks, expectedChunks),
      failures: failures.length,
    },
    failures,
  };
}

export async function GET() {
  if (!(await isAdmin())) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  try {
    return NextResponse.json(await migrationProgress(), { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Admin migration progress failed:", error);
    return NextResponse.json({ error: "Could not load migration progress" }, { status: 500 });
  }
}

export async function POST(request: Request) {
  if (!(await isAdmin())) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  const body: unknown = await request.json().catch(() => ({}));
  const sourceId = body && typeof body === "object" && !Array.isArray(body)
    ? (body as { sourceId?: unknown }).sourceId
    : undefined;
  if (typeof sourceId !== "number" || !Number.isSafeInteger(sourceId) || sourceId <= 0) {
    return NextResponse.json({ error: "Invalid Source id" }, { status: 400 });
  }
  try {
    const requeued = await requeueFailedBackfillJob(sourceId);
    if (!requeued) return NextResponse.json({ error: "This backfill job cannot be retried" }, { status: 409 });
    if (env.INGEST_ENGINE === "python") after(async () => void (await fireTick()));
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Admin migration retry failed:", error);
    return NextResponse.json({ error: "Could not retry this backfill job" }, { status: 500 });
  }
}

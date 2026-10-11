import { baseUrl, callIntelligence } from "@/lib/intelligence-client";
import { env } from "@/lib/env";

/** Safety valve for the self-re-firing chain (each hop is one leased step of up to ~240 s). */
export const MAX_TICK_HOPS = 200;
const STEP_TIMEOUT_MS = 295_000;
const NIL_WORKSPACE = "00000000-0000-0000-0000-000000000000";

export interface StepResult {
  claimed: boolean;
  outcome?: string;
  reason?: string;
  more: boolean;
  jobId?: number;
  errorCode?: string;
}

/** Ask Python to claim and run ONE job. The workspace comes from the claimed row, not from this token. */
export async function runStep(): Promise<StepResult> {
  const response = await callIntelligence(
    "/ingest/step",
    { sub: "ingest-tick", wid: NIL_WORKSPACE, scope: "ingest_step" },
    { method: "POST", signal: AbortSignal.timeout(STEP_TIMEOUT_MS) },
  );
  if (!response.ok) throw new Error(`ingest step failed: HTTP ${response.status}`);
  const body = (await response.json()) as Record<string, unknown>;
  return {
    claimed: body.claimed === true,
    outcome: typeof body.outcome === "string" ? body.outcome : undefined,
    reason: typeof body.reason === "string" ? body.reason : undefined,
    more: body.more === true,
    jobId: typeof body.job_id === "number" ? body.job_id : undefined,
    errorCode: typeof body.error_code === "string" ? body.error_code : undefined,
  };
}

export async function runSweep(): Promise<{ swept: number; more: boolean }> {
  const response = await callIntelligence(
    "/ingest/sweep",
    { sub: "ingest-sweep", wid: NIL_WORKSPACE, scope: "ingest_step" },
    { method: "POST", signal: AbortSignal.timeout(60_000) },
  );
  if (!response.ok) throw new Error(`ingest sweep failed: HTTP ${response.status}`);
  const body = (await response.json()) as { swept?: number; more?: boolean };
  return { swept: body.swept ?? 0, more: body.more === true };
}

export interface PurgeSummary {
  listed: number;
  purged: number;
  retryScheduled: number;
}

/** Purge soft-deleted Sources past the grace period (blobs of every version, then rows). Runs from the daily sweep. */
export async function runPurge(): Promise<PurgeSummary> {
  const response = await callIntelligence(
    "/ingest/purge",
    { sub: "ingest-purge", wid: NIL_WORKSPACE, scope: "ingest_step" },
    { method: "POST", signal: AbortSignal.timeout(55_000) },
  );
  if (!response.ok) throw new Error(`ingest purge failed: HTTP ${response.status}`);
  const body = (await response.json()) as { listed?: number; purged?: number; retry_scheduled?: number };
  return { listed: body.listed ?? 0, purged: body.purged ?? 0, retryScheduled: body.retry_scheduled ?? 0 };
}

/**
 * Start the next tick. The tick route answers 202 immediately and does its work in after(), so awaiting this
 * fetch only waits for the acknowledgement, not for a 240 s step.
 */
export async function fireTick(hop = 0): Promise<boolean> {
  const secret = env.CRON_SECRET;
  if (!secret || hop >= MAX_TICK_HOPS) return false;
  const headers: Record<string, string> = { authorization: `Bearer ${secret}` };
  if (env.VERCEL_AUTOMATION_BYPASS_SECRET) headers["x-vercel-protection-bypass"] = env.VERCEL_AUTOMATION_BYPASS_SECRET;
  try {
    const res = await fetch(`${baseUrl()}/api/internal/ingest/tick?hop=${hop + 1}`, {
      method: "POST",
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

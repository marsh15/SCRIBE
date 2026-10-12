import { after, NextResponse } from "next/server";
import { hasCronSecret } from "@/lib/cron-auth";
import { fireTick, runPurge, runSweep } from "@/lib/ingestion/tick";
import { internalError } from "@/lib/api-errors";
import { purgeSoftDeletedSources, pythonEngineConfigured } from "@/lib/ingestion/source-delete";

export const maxDuration = 60;

/** Daily cron (Hobby): requeue expired leases, purge soft-deleted Sources, then kick a tick if anything is claimable (incl. due retries). */
async function sweep(req: Request) {
  if (!hasCronSecret(req)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  try {
    if (!pythonEngineConfigured()) {
      // Legacy engine (default) or no INTERNAL_SERVICE_SECRET: there is no Python service to sweep or purge. Hard-delete
      // any Source still soft-deleted by the interim behaviour (bounded, idempotent) and answer 200, never 500.
      let purge: Awaited<ReturnType<typeof purgeSoftDeletedSources>> | { error: string };
      try {
        purge = await purgeSoftDeletedSources();
      } catch (error) {
        console.error("[ingestion-sweep] legacy purge failed", error instanceof Error ? error.name : "unknown");
        purge = { error: "purge_failed" };
      }
      return NextResponse.json({
        ok: true,
        swept: 0,
        more: false,
        note: "python engine not configured",
        purge,
      });
    }
    const result = await runSweep();
    if (result.more) after(async () => void (await fireTick()));
    // T20: purge soft-deleted Sources past the grace period. A purge failure must not fail the lease sweep; it is
    // recorded on the Source (purge_error_code) and reported here.
    let purge: Awaited<ReturnType<typeof runPurge>> | { error: string };
    try {
      purge = await runPurge();
    } catch (error) {
      console.error("[ingestion-sweep] purge failed", error instanceof Error ? error.message : "unknown");
      purge = { error: "purge_failed" };
    }
    return NextResponse.json({ ok: true, ...result, purge });
  } catch (error) {
    return internalError("ingestion-sweep", error);
  }
}

export const GET = sweep;
export const POST = sweep;

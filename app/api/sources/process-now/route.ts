import { after, NextResponse } from "next/server";
import { isNotAuthenticatedError } from "@/lib/auth";
import { resolveWorkspace } from "@/lib/workspace";
import { internalError } from "@/lib/api-errors";
import { runQueuedSourceIntake } from "@/lib/ingestion/source-intake";
import { env } from "@/lib/env";
import { fireTick } from "@/lib/ingestion/tick";
import { markSourceDue } from "@/lib/ingestion/python-queue";

function parseSourceId(value: unknown) {
  if (value === undefined || value === null || value === "") return undefined;
  const sourceId = Number(value);
  if (!Number.isInteger(sourceId) || sourceId <= 0) {
    throw new Error("Invalid Source id");
  }
  return sourceId;
}

export const maxDuration = 300;

export async function POST(req: Request) {
  try {
    const { userId, workspaceId } = await resolveWorkspace();
    const body = (await req.json().catch(() => ({}))) as { sourceId?: unknown };
    const sourceId = parseSourceId(body.sourceId);
    if (env.INGEST_ENGINE === "python") {
      // Leased steps run in the tick chain (T18); answer now instead of holding this request for a whole step.
      if (sourceId) await markSourceDue(workspaceId, sourceId);
      after(async () => void (await fireTick()));
      return NextResponse.json({ ok: true, ticked: true, claimed: 0, ready: 0, retrying: 0, failed: 0 });
    }
    const result = await runQueuedSourceIntake(sourceId ? 1 : 3, { userId, workspaceId, sourceId });

    return NextResponse.json({ ok: true, ...result });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    if (error instanceof Error && error.message === "Invalid Source id") {
      return NextResponse.json({ error: error.message }, { status: 400 });
    }
    return internalError("user-source-intake", error);
  }
}

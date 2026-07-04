import { NextResponse } from "next/server";
import { getUserId, isNotAuthenticatedError } from "@/lib/auth";
import { internalError } from "@/lib/api-errors";
import { runQueuedSourceIntake } from "@/lib/ingestion/source-intake";

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
    const userId = await getUserId();
    const body = (await req.json().catch(() => ({}))) as { sourceId?: unknown };
    const sourceId = parseSourceId(body.sourceId);
    const result = await runQueuedSourceIntake(sourceId ? 1 : 3, { userId, sourceId });

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

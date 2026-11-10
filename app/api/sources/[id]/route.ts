import { NextResponse } from "next/server";
import { getUserId, isNotAuthenticatedError } from "@/lib/auth";
import { internalError } from "@/lib/api-errors";
import { deleteSource } from "@/lib/ingestion/source-intake";

/**
 * Delete one Source. Same operation as the "delete" button (a server action), exposed so the preview smoke script can
 * exercise the delete lifecycle over HTTP. The caller's workspace is derived on the server; an id from another workspace
 * answers 404 exactly like an unknown id.
 */
export async function DELETE(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  try {
    const userId = await getUserId();
    const sourceId = Number((await ctx.params).id);
    if (!Number.isInteger(sourceId) || sourceId <= 0) {
      return NextResponse.json({ error: "Invalid Source id" }, { status: 400 });
    }
    const deleted = await deleteSource(userId, sourceId);
    if (!deleted) return NextResponse.json({ error: "Source not found" }, { status: 404 });
    return NextResponse.json({ ok: true });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    return internalError("source-delete", error);
  }
}

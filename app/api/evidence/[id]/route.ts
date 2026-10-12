import { NextResponse } from "next/server";
import { isNotAuthenticatedError } from "@/lib/auth";
import { internalError } from "@/lib/api-errors";
import { resolveWorkspace } from "@/lib/workspace";
import { isUuid, openEvidence } from "@/lib/evidence/open";

export const dynamic = "force-dynamic";

const NOT_FOUND = { error: { code: "NOT_FOUND", message: "Evidence not found." } };

/**
 * Open one citation (T23). Workspace access is re-checked on EVERY open by the Python service (INV-8): an id from
 * another workspace answers 404 exactly like an unknown id. A deleted Source returns `{sourceDeleted: true}` with the
 * quote withheld. The response is never cached.
 */
export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { userId, workspaceId } = await resolveWorkspace();
    const { id } = await params;
    if (!isUuid(id)) return NextResponse.json(NOT_FOUND, { status: 404 });
    const result = await openEvidence(id, { userId, workspaceId });
    if (!result.ok) {
      if (result.status === 404) return NextResponse.json(NOT_FOUND, { status: 404 });
      return NextResponse.json(
        { error: { code: "EVIDENCE_UNAVAILABLE", message: "The citation could not be opened right now. Please retry." } },
        { status: 502, headers: { "cache-control": "no-store" } },
      );
    }
    return NextResponse.json(result.evidence, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: { code: "UNAUTHENTICATED", message: "Sign in required." } }, { status: 401 });
    }
    return internalError("evidence-open", error);
  }
}

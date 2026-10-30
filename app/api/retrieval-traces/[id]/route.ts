import { NextResponse } from "next/server";
import { isNotAuthenticatedError } from "@/lib/auth";
import { resolveWorkspace } from "@/lib/workspace";
import { callIntelligence } from "@/lib/intelligence-client";
import { isUuid } from "@/lib/evidence/open";
import { parseRetrievalTraceResponse } from "@/lib/evidence/trace-response";

export const dynamic = "force-dynamic";
const headers = { "cache-control": "no-store" };
const notFound = () => NextResponse.json(
  { error: { code: "NOT_FOUND", message: "Retrieval trace not found." } }, { status: 404, headers },
);
const unavailable = () => NextResponse.json(
  { error: { code: "TRACE_UNAVAILABLE", message: "Search diagnostics could not be loaded. Please retry." } },
  { status: 502, headers },
);

/** Reauthorize this exact trace in the session workspace on every inspection. Never use the latest chat trace. */
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { userId, workspaceId } = await resolveWorkspace();
    const { id } = await params;
    if (!isUuid(id)) return notFound();
    const response = await callIntelligence(
      "/retrieval/trace",
      { sub: userId, wid: workspaceId, scope: "retrieval_trace_read" },
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ trace_id: id }),
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (response.status === 404) return notFound();
    if (!response.ok) return unavailable();
    const details = parseRetrievalTraceResponse(await response.json());
    if (!details || details.trace.id.toLowerCase() !== id.toLowerCase()) return unavailable();
    return NextResponse.json(details, { headers });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json(
        { error: { code: "UNAUTHENTICATED", message: "Sign in required." } }, { status: 401, headers },
      );
    }
    // Provider/parser errors can carry query or document text. Return a generic failure without logging the payload.
    return unavailable();
  }
}

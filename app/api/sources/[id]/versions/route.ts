import { NextResponse } from "next/server";
import { getUserId, isNotAuthenticatedError } from "@/lib/auth";
import { env } from "@/lib/env";
import { SourceIntakeError } from "@/lib/ingestion/source-intake-policy";
import { reserveSourceVersion } from "@/lib/ingestion/source-versions";

function appBaseUrl(req: Request) {
  const configured = process.env.NEXT_PUBLIC_APP_URL?.trim();
  if (configured) {
    for (const candidate of [configured, `https://${configured}`]) {
      try {
        return new URL(candidate).origin;
      } catch {
        // try the next candidate, then the request origin
      }
    }
  }
  return new URL(req.url).origin;
}

/** Reserve the next version of a Source ("Upload new version"). The old version stays searchable meanwhile. */
export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }) {
  try {
    const userId = await getUserId();
    const sourceId = Number((await ctx.params).id);
    if (env.INGEST_ENGINE !== "python") {
      return NextResponse.json(
        { error: "Versioned uploads need the Python indexer (INGEST_ENGINE=python).", code: "engine_unsupported" },
        { status: 409 }
      );
    }
    const metadata = await req.json();
    const callbackUrl = new URL("/api/sources/upload-complete", appBaseUrl(req)).toString();
    const reservation = await reserveSourceVersion({ userId, sourceId, metadata, callbackUrl });
    return NextResponse.json({ ok: true, ...reservation });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    if (error instanceof SourceIntakeError) {
      return NextResponse.json({ error: error.message, code: error.code }, { status: error.status });
    }
    if (error instanceof Error && error.message.includes("BLOB_READ_WRITE_TOKEN")) {
      return NextResponse.json(
        { error: "Private uploads are not configured.", code: "missing_blob_config" },
        { status: 503 }
      );
    }
    console.error("[SourceVersion] reservation failed", error);
    return NextResponse.json({ error: "Could not prepare the new version upload" }, { status: 503 });
  }
}

import { NextResponse } from "next/server";
import { getUserId, isNotAuthenticatedError } from "@/lib/auth";
import {
  reserveSourceUpload,
  SourceIntakeError,
} from "@/lib/ingestion/source-intake";

function appBaseUrl(req: Request) {
  const configured = process.env.NEXT_PUBLIC_APP_URL?.trim();
  if (configured) {
    for (const candidate of [configured, `https://${configured}`]) {
      try {
        return new URL(candidate).origin;
      } catch {
        // Try the next candidate; fall back to the request origin below.
      }
    }
    console.warn("[SourceIntake] invalid NEXT_PUBLIC_APP_URL, falling back to request origin", {
      configured,
    });
  }

  return new URL(req.url).origin;
}

function isMissingBlobConfig(error: unknown) {
  return error instanceof Error && error.message.includes("BLOB_READ_WRITE_TOKEN");
}

function isSchemaOutOfDate(error: unknown) {
  if (!(error instanceof Error)) return false;
  const message = error.message.toLowerCase();
  return (
    message.includes("column") && message.includes("does not exist") ||
    message.includes("relation") && message.includes("does not exist") ||
    message.includes("files_status_check") ||
    message.includes("ingestion_jobs_file_id_unique")
  );
}

export async function POST(req: Request) {
  try {
    const userId = await getUserId();
    const metadata = await req.json();
    const callbackUrl = new URL("/api/sources/upload-complete", appBaseUrl(req)).toString();
    const reservation = await reserveSourceUpload({
      userId,
      metadata,
      callbackUrl,
    });
    return NextResponse.json({ ok: true, ...reservation });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    if (error instanceof SourceIntakeError) {
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: error.status }
      );
    }
    if (isMissingBlobConfig(error)) {
      return NextResponse.json(
        {
          error:
            "Private uploads are not configured. Add BLOB_READ_WRITE_TOKEN in Vercel and redeploy.",
          code: "missing_blob_config",
        },
        { status: 503 }
      );
    }
    if (isSchemaOutOfDate(error)) {
      return NextResponse.json(
        {
          error:
            "The database schema is out of date. Apply drizzle/0006_deepen_source_intake.sql, then retry the upload.",
          code: "schema_out_of_date",
        },
        { status: 503 }
      );
    }
    console.error("[SourceIntake] reservation failed", error);
    return NextResponse.json(
      { error: "Could not prepare the private Source upload" },
      { status: 503 }
    );
  }
}

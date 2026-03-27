import { NextResponse } from "next/server";
import { getUserId } from "@/lib/auth";
import { db } from "@/lib/db-config";
import { files } from "@/lib/db-schema";
import { getUsageSummary } from "@/lib/billing/usage";
import { getUserMaxUploadBytes } from "@/lib/uploads/limits";
import { withDatabaseRetry } from "@/lib/db-retry";

/**
 * POST /api/ingest/batch/init
 *
 * Lightweight endpoint for the browser-orchestrated pipeline.
 * Creates a file record with status 'processing' WITHOUT uploading the raw file.
 * The browser will then POST text chunks directly to /api/ingest/batch.
 *
 * Body: { fileName, fileSize, fileType }
 */
export async function POST(req: Request) {
  try {
    const userId = await getUserId();

    const body = await req.json();
    const { fileName, fileSize, fileType } = body as {
      fileName: string;
      fileSize: number;
      fileType: string;
    };

    if (!fileName || !fileSize || !fileType) {
      return NextResponse.json(
        { error: "Missing required fields: fileName, fileSize, fileType." },
        { status: 400 }
      );
    }

    // ---- Quota check ----
    const usage = await getUsageSummary(userId);
    if (!usage.allowOverage && usage.projectedOverageInr > 0) {
      return NextResponse.json(
        { error: "Free plan usage limit reached. Upgrade to upload more files." },
        { status: 402 }
      );
    }

    // ---- File size limit check ----
    const { maxBytes, maxMb, planCode } = await getUserMaxUploadBytes(userId);
    if (fileSize > maxBytes) {
      return NextResponse.json(
        {
          error: `File exceeds plan limit (${maxMb} MB for ${planCode.toUpperCase()} plan).`,
        },
        { status: 413 }
      );
    }

    // ---- Create file record with status 'processing' (no raw file data) ----
    const [insertedFile] = await withDatabaseRetry("initBrowserFile", () =>
      db
        .insert(files)
        .values({
          name: fileName,
          type: fileType,
          size: fileSize,
          userId,
          status: "processing",
          // No fileData, storageKey, or storageUrl — raw file stays in the browser
        })
        .returning()
    );

    return NextResponse.json({
      ok: true,
      file: {
        id: insertedFile.id,
        name: insertedFile.name,
        size: insertedFile.size,
        status: "processing",
      },
    });
  } catch (error) {
    console.error("Batch init error:", error);
    const message =
      error instanceof Error ? error.message : "Failed to initialize file";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db, sql } from "@/lib/db-config";
import { files, ingestionJobs } from "@/lib/db-schema";
import { getUserId } from "@/lib/auth";
import { verifyUploadToken } from "@/lib/uploads/signature";
import { uploadBufferToBlob } from "@/lib/storage/blob";
import { recordUsageEvent } from "@/lib/billing/usage";
import { ingestFile } from "@/lib/ingestion/worker";
import { flags } from "@/lib/flags";
import { withDatabaseRetry } from "@/lib/db-retry";

const DEV_FILEDATA_MAX_BYTES = 25 * 1024 * 1024;
export const maxDuration = 60;

const ALLOWED_MIME_TYPES = new Set([
  "application/pdf",
  "text/plain",
  "text/csv",
  "text/markdown",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

function makeDataUri(fileType: string, bytes: Buffer) {
  return `data:${fileType};base64,${bytes.toString("base64")}`;
}

function formatDatabaseError(error: unknown) {
  if (!error || typeof error !== "object") {
    return "Unknown database error";
  }

  const details = error as {
    message?: string;
    code?: string;
    detail?: string;
    table?: string;
    column?: string;
    constraint?: string;
  };

  return [
    details.message,
    details.code ? `code=${details.code}` : null,
    details.table ? `table=${details.table}` : null,
    details.column ? `column=${details.column}` : null,
    details.constraint ? `constraint=${details.constraint}` : null,
    details.detail ?? null,
  ]
    .filter(Boolean)
    .join(" | ");
}

async function insertQueuedFileRecord(input: {
  name: string;
  type: string;
  size: number;
  userId: string;
  fileData: string | null;
  storageKey: string | null;
  storageUrl: string | null;
}) {
  try {
    const [insertedFile] = await withDatabaseRetry("insertQueuedFileRecord", () =>
      db
        .insert(files)
        .values({
          name: input.name,
          type: input.type,
          size: input.size,
          userId: input.userId,
          fileData: input.fileData,
          storageKey: input.storageKey,
          storageUrl: input.storageUrl,
          status: "queued",
        })
        .returning()
    );

    return insertedFile;
  } catch (primaryError) {
    console.error("Primary file insert failed; retrying with raw SQL:", {
      error: formatDatabaseError(primaryError),
    });

    try {
      const result = await withDatabaseRetry("insertQueuedFileRecordRaw", () =>
        sql`
          insert into "files" ("name", "type", "size", "user_id", "file_data", "storage_key", "storage_url", "status")
          values (${input.name}, ${input.type}, ${input.size}, ${input.userId}, ${input.fileData}, ${input.storageKey}, ${input.storageUrl}, 'queued')
          returning "id", "name", "type", "size", "user_id", "file_data", "extracted_text", "storage_key", "storage_url", "status", "processing_error", "text_bytes", "created_at"
        `
      );

      // neon-http returns rows as an array directly
      const row = result[0];
      if (!row) {
        throw new Error("Raw SQL insert returned no file row");
      }

      return {
        id: row.id,
        name: row.name,
        type: row.type,
        size: row.size,
        userId: row.user_id,
        fileData: row.file_data,
        extractedText: row.extracted_text,
        storageKey: row.storage_key,
        storageUrl: row.storage_url,
        status: row.status,
        processingError: row.processing_error,
        textBytes: row.text_bytes,
        createdAt: row.created_at,
      };
    } catch (fallbackError) {
      console.error("Raw SQL file insert failed:", {
        error: formatDatabaseError(fallbackError),
      });

      throw new Error(formatDatabaseError(fallbackError) || formatDatabaseError(primaryError));
    }
  }
}


export async function POST(req: Request) {
  try {
    const userId = await getUserId();
    const formData = await req.formData();

    const uploadToken = String(formData.get("uploadToken") ?? "");
    const file = formData.get("file") as File | null;

    if (!uploadToken || !file) {
      return NextResponse.json({ error: "Missing upload token or file" }, { status: 400 });
    }

    const tokenPayload = verifyUploadToken(uploadToken);
    if (!tokenPayload) {
      return NextResponse.json({ error: "Invalid or expired upload token" }, { status: 401 });
    }

    if (tokenPayload.userId !== userId) {
      return NextResponse.json({ error: "Upload token is not valid for this user" }, { status: 403 });
    }

    // Server-side file type allowlist — reject before wasting blob storage or DB rows
    if (!ALLOWED_MIME_TYPES.has(file.type)) {
      return NextResponse.json(
        { error: `Unsupported file type: ${file.type || "unknown"}. Allowed types: PDF, TXT, CSV, Markdown, DOCX.` },
        { status: 415 }
      );
    }

    if (
      tokenPayload.fileName !== file.name ||
      tokenPayload.fileType !== file.type ||
      tokenPayload.fileSize !== file.size
    ) {
      return NextResponse.json({ error: "File metadata mismatch" }, { status: 400 });
    }

    const bytes = Buffer.from(await file.arrayBuffer());

    let storageKey: string | null = null;
    let storageUrl: string | null = null;
    let fileData: string | null = null;

    const useBlob = Boolean(process.env.BLOB_READ_WRITE_TOKEN);

    if (useBlob) {
      const pathname = `uploads/${userId}/${Date.now()}-${file.name}`;
      const uploaded = await uploadBufferToBlob({
        pathname,
        contentType: file.type || "application/octet-stream",
        body: bytes,
      });
      storageKey = uploaded.pathname;
      storageUrl = uploaded.url;
    } else if (file.size <= DEV_FILEDATA_MAX_BYTES) {
      fileData = makeDataUri(file.type || "application/octet-stream", bytes);
    } else {
      return NextResponse.json(
        {
          error:
            "BLOB_READ_WRITE_TOKEN is required for files larger than 25 MB in this environment.",
        },
        { status: 500 }
      );
    }

    const insertedFile = await insertQueuedFileRecord({
      name: file.name,
      type: file.type || "application/octet-stream",
      size: file.size,
      userId,
      fileData,
      storageKey,
      storageUrl,
    });

    const storageMilliGbDay = Math.ceil((file.size / (1024 * 1024 * 1024)) * 1000);
    await recordUsageEvent({
      userId,
      metric: "storage_gb_day",
      quantity: storageMilliGbDay,
      unit: "gb_day",
      sourceType: "upload",
      sourceId: String(insertedFile.id),
      isEstimated: true,
    });

    // ---- Browser-orchestrated pipeline: skip server-side ingestion ----
    const skipIngestion = String(formData.get("skipIngestion") ?? "") === "true";
    if (skipIngestion) {
      // Mark as 'processing' — the browser will upload chunks via /api/ingest/batch
      await withDatabaseRetry("markFileProcessingForBrowser", () =>
        db
          .update(files)
          .set({ status: "processing" })
          .where(eq(files.id, insertedFile.id))
      );

      return NextResponse.json({
        ok: true,
        file: {
          id: insertedFile.id,
          name: insertedFile.name,
          size: insertedFile.size,
          status: "processing",
        },
        ingestionMode: "browser",
      });
    }

    if (flags.asyncIngestionEnabled) {
      try {
        const [job] = await withDatabaseRetry("enqueueIngestionJob", () =>
          db
            .insert(ingestionJobs)
            .values({
              fileId: insertedFile.id,
              status: "queued",
              attempts: 0,
            })
            .returning()
        );

        return NextResponse.json({
          ok: true,
          file: {
            id: insertedFile.id,
            name: insertedFile.name,
            size: insertedFile.size,
            status: insertedFile.status,
          },
          ingestionJobId: job.id,
          ingestionMode: "async",
        });
      } catch (queueError) {
        console.error("Falling back to direct ingestion after queue insert failure:", queueError);
      }
    }

    const ingestionResult = await ingestFile(insertedFile.id);

    if (!ingestionResult.processed) {
      return NextResponse.json(
        {
          ok: false,
          error: ingestionResult.reason,
          file: {
            id: insertedFile.id,
            name: insertedFile.name,
            size: insertedFile.size,
            status: "failed",
            processingError: ingestionResult.reason,
          },
          ingestionMode: "direct",
        },
        { status: 500 },
      );
    }

    return NextResponse.json({
      ok: true,
      file: {
        id: insertedFile.id,
        name: insertedFile.name,
        size: insertedFile.size,
        status: "ready",
      },
      ingestionMode: "direct",
      chunks: ingestionResult.chunks,
    });
  } catch (error) {
    console.error("Upload complete error:", error);
    const message =
      error instanceof Error ? error.message : "Failed to complete upload";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

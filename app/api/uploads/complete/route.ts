import { NextResponse } from "next/server";
import { db } from "@/lib/db-config";
import { files, ingestionJobs } from "@/lib/db-schema";
import { getUserId } from "@/lib/auth";
import { verifyUploadToken } from "@/lib/uploads/signature";
import { uploadBufferToBlob } from "@/lib/storage/blob";
import { recordUsageEvent } from "@/lib/billing/usage";

const DEV_FILEDATA_MAX_BYTES = 25 * 1024 * 1024;

function makeDataUri(fileType: string, bytes: Buffer) {
  return `data:${fileType};base64,${bytes.toString("base64")}`;
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

    const [insertedFile] = await db
      .insert(files)
      .values({
        name: file.name,
        type: file.type || "application/octet-stream",
        size: file.size,
        userId,
        fileData,
        storageKey,
        storageUrl,
        status: "queued",
      })
      .returning();

    const [job] = await db
      .insert(ingestionJobs)
      .values({
        fileId: insertedFile.id,
        status: "queued",
        attempts: 0,
      })
      .returning();

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

    return NextResponse.json({
      ok: true,
      file: {
        id: insertedFile.id,
        name: insertedFile.name,
        size: insertedFile.size,
        status: insertedFile.status,
      },
      ingestionJobId: job.id,
    });
  } catch (error) {
    console.error("Upload complete error:", error);
    return NextResponse.json({ error: "Failed to complete upload" }, { status: 500 });
  }
}

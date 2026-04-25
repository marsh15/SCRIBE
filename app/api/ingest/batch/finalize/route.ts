import { NextResponse } from "next/server";
import { getUserId, isNotAuthenticatedError } from "@/lib/auth";
import { db } from "@/lib/db-config";
import { documents, files } from "@/lib/db-schema";
import { eq } from "drizzle-orm";
import { withDatabaseRetry } from "@/lib/db-retry";
import { analyzeFinalizeState } from "@/lib/ingestion/batch";

interface FinalizeBody {
  fileId: number;
  totalChunks: number;
  textBytes: number;
  extractedText?: string;
}

export async function POST(req: Request) {
  try {
    const userId = await getUserId();
    const body: FinalizeBody = await req.json();
    const { fileId, totalChunks, textBytes, extractedText } = body;

    if (!Number.isInteger(fileId) || fileId <= 0) {
      return NextResponse.json(
        { error: "Missing or invalid fileId." },
        { status: 400 }
      );
    }

    if (!Number.isInteger(totalChunks) || totalChunks <= 0) {
      return NextResponse.json(
        { error: "Missing or invalid totalChunks." },
        { status: 400 }
      );
    }

    // ---- File ownership check ----
    const file = await withDatabaseRetry("finalizeLoadFile", () =>
      db.query.files.findFirst({
        where: eq(files.id, fileId),
      })
    );

    if (!file) {
      return NextResponse.json({ error: "File not found." }, { status: 404 });
    }

    if (file.userId !== userId) {
      return NextResponse.json(
        { error: "You do not own this file." },
        { status: 403 }
      );
    }

    const storedChunks = await withDatabaseRetry("finalizeLoadChunkIndexes", () =>
      db
        .select({ chunkIndex: documents.chunkIndex })
        .from(documents)
        .where(eq(documents.fileId, fileId))
        .orderBy(documents.chunkIndex)
    );

    const finalizeState = analyzeFinalizeState(
      storedChunks.map((chunk) => chunk.chunkIndex),
      totalChunks
    );

    if (!finalizeState.isComplete) {
      return NextResponse.json(
        {
          error: "File ingestion is incomplete. Some chunk batches are still missing.",
          storedChunkCount: finalizeState.storedChunkCount,
          expectedChunkCount: totalChunks,
          missingChunkIndexes: finalizeState.missingChunkIndexes.slice(0, 20),
        },
        { status: 409 }
      );
    }

    if (file.status === "ready") {
      return NextResponse.json({
        ok: true,
        fileId,
        status: "ready",
        totalChunks,
      });
    }

    if (file.status !== "processing") {
      return NextResponse.json(
        { error: `File is in '${file.status}' state, expected 'processing'.` },
        { status: 409 }
      );
    }

    // ---- Mark file as ready + save extracted text for preview ----
    await withDatabaseRetry("finalizeMarkReady", () =>
      db
        .update(files)
        .set({
          status: "ready",
          processingError: null,
          textBytes: textBytes || 0,
          // Save extracted text (truncated to first 100K chars to avoid DB bloat)
          ...(extractedText
            ? { extractedText: extractedText.substring(0, 100_000) }
            : {}),
        })
        .where(eq(files.id, fileId))
    );

    return NextResponse.json({
      ok: true,
      fileId,
      status: "ready",
      totalChunks,
    });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    console.error("Batch finalize error:", error);
    const message =
      error instanceof Error ? error.message : "Finalization failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

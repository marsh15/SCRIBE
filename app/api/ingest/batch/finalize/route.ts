import { NextResponse } from "next/server";
import { getUserId } from "@/lib/auth";
import { db } from "@/lib/db-config";
import { files } from "@/lib/db-schema";
import { eq } from "drizzle-orm";
import { withDatabaseRetry } from "@/lib/db-retry";

interface FinalizeBody {
  fileId: number;
  totalChunks: number;
  textBytes: number;
}

export async function POST(req: Request) {
  try {
    const userId = await getUserId();
    const body: FinalizeBody = await req.json();
    const { fileId, totalChunks, textBytes } = body;

    if (!fileId) {
      return NextResponse.json(
        { error: "Missing fileId." },
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

    if (file.status !== "processing") {
      return NextResponse.json(
        { error: `File is in '${file.status}' state, expected 'processing'.` },
        { status: 409 }
      );
    }

    // ---- Mark file as ready ----
    await withDatabaseRetry("finalizeMarkReady", () =>
      db
        .update(files)
        .set({
          status: "ready",
          processingError: null,
          textBytes: textBytes || 0,
        })
        .where(eq(files.id, fileId))
    );

    return NextResponse.json({
      ok: true,
      fileId,
      status: "ready",
      totalChunks: totalChunks || 0,
    });
  } catch (error) {
    console.error("Batch finalize error:", error);
    const message =
      error instanceof Error ? error.message : "Finalization failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

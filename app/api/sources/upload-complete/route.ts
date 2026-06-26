import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";
import { NextResponse } from "next/server";
import {
  completeSourceUpload,
  parseSourceUploadCompletionPayload,
  SourceIntakeError,
} from "@/lib/ingestion/source-intake";
import { getBlobMetadata } from "@/lib/storage/blob";

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as HandleUploadBody;
    const result = await handleUpload({
      request: req,
      body,
      onBeforeGenerateToken: async () => {
        throw new Error("Client tokens are issued by the Source reservation route");
      },
      onUploadCompleted: async ({ blob, tokenPayload }) => {
        const metadata = await getBlobMetadata(blob.url);
        await completeSourceUpload({
          payload: parseSourceUploadCompletionPayload(tokenPayload),
          blob: {
            url: blob.url,
            pathname: blob.pathname,
            size: metadata.size,
            contentType: metadata.contentType,
          },
        });
      },
    });
    return NextResponse.json(result);
  } catch (error) {
    if (error instanceof SourceIntakeError) {
      console.error("[SourceIntake] completion rejected", {
        code: error.code,
        message: error.message,
      });
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: error.status }
      );
    }
    console.error("[SourceIntake] completion failed", error);
    return NextResponse.json({ error: "Upload completion failed" }, { status: 500 });
  }
}

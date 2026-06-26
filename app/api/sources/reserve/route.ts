import { NextResponse } from "next/server";
import { getUserId, isNotAuthenticatedError } from "@/lib/auth";
import {
  reserveSourceUpload,
  SourceIntakeError,
} from "@/lib/ingestion/source-intake";

export async function POST(req: Request) {
  try {
    const userId = await getUserId();
    const metadata = await req.json();
    const appUrl = process.env.NEXT_PUBLIC_APP_URL?.trim() || req.url;
    const callbackUrl = new URL("/api/sources/upload-complete", appUrl).toString();
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
    console.error("[SourceIntake] reservation failed", error);
    return NextResponse.json(
      { error: "Could not prepare the private Source upload" },
      { status: 503 }
    );
  }
}

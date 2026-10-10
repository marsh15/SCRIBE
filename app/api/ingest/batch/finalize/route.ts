import { NextResponse } from "next/server";

// Removed in Scribe 2.0 (T04): Sources are ingested only through the private
// Blob intake in lib/ingestion/source-intake.ts (see ADR-0001).
function gone() {
  return NextResponse.json(
    { error: "This endpoint was removed. Upload Sources through the Sources page." },
    { status: 410 }
  );
}

export const GET = gone;
export const POST = gone;
export const PUT = gone;
export const DELETE = gone;

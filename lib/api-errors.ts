import { NextResponse } from "next/server";

export function internalError(context: string, error: unknown) {
  const requestId = crypto.randomUUID();
  console.error(`[${context}]`, { requestId, error });
  return NextResponse.json(
    { error: { code: "INTERNAL_ERROR", message: "The request could not be completed.", requestId } },
    { status: 500 },
  );
}

import { NextResponse } from "next/server";
import { getUserId, isNotAuthenticatedError } from "@/lib/auth";
import { getLatestRagTraceView } from "@/lib/rag-observability";

export async function GET(req: Request) {
  try {
    const userId = await getUserId();
    const url = new URL(req.url);
    const chatId = url.searchParams.get("chatId") || undefined;
    const assistantMessageId =
      url.searchParams.get("assistantMessageId") || undefined;

    if (!chatId && !assistantMessageId) {
      return NextResponse.json(
        { error: "chatId or assistantMessageId is required" },
        { status: 400 }
      );
    }

    const trace = await getLatestRagTraceView({
      userId,
      chatId,
      assistantMessageId,
    });

    return NextResponse.json({ trace });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }

    console.error("[RAG] Failed to fetch latest trace:", error);
    return NextResponse.json(
      { error: "Failed to fetch latest RAG trace" },
      { status: 500 }
    );
  }
}

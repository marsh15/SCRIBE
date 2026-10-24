import { and, desc, eq } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";
import { db } from "@/lib/db-config";
import { answerFeedback, chatMessages, chats, ragTraces, retrievalTraces } from "@/lib/db-schema";
import { internalError } from "@/lib/api-errors";
import { isNotAuthenticatedError } from "@/lib/auth";
import { scoped } from "@/lib/scoped";
import { resolveWorkspace } from "@/lib/workspace";
import { latestTraceLinkCandidates } from "./trace-links";

const FeedbackSchema = z.object({
  messageId: z.string().trim().min(1).max(200),
  rating: z.enum(["up", "down"]),
  reasons: z.array(z.enum(["wrong_citation", "missing_info", "outdated_version", "should_abstain"]))
    .max(4)
    .refine((reasons) => new Set(reasons).size === reasons.length, "Reasons must be unique")
    .optional(),
  comment: z.string().max(2000).optional(),
}).strict();

interface MessageRow {
  id: string;
  chatId: string;
  role: string;
  parts: unknown;
}

export async function POST(request: Request) {
  try {
    const rawBody: unknown = await request.json().catch(() => undefined);
    const parsed = FeedbackSchema.safeParse(rawBody);
    if (!parsed.success) {
      return NextResponse.json({ error: "Invalid feedback" }, { status: 400 });
    }

    const { userId, workspaceId } = await resolveWorkspace();
    const [messageRows] = await scoped(workspaceId).batch([
      db.select({
        id: chatMessages.id,
        chatId: chatMessages.chatId,
        role: chatMessages.role,
        parts: chatMessages.parts,
      })
        .from(chatMessages)
        .innerJoin(chats, eq(chatMessages.chatId, chats.id))
        .where(and(
          eq(chatMessages.id, parsed.data.messageId),
          eq(chatMessages.role, "assistant"),
          eq(chats.userId, userId),
          eq(chats.workspaceId, workspaceId),
        ))
        .limit(1) as never,
    ]) as unknown as [MessageRow[]];
    const message = messageRows?.[0];
    if (!message) return NextResponse.json({ error: "Answer not found" }, { status: 404 });

    const traceCandidates = latestTraceLinkCandidates(message.parts);
    const emptyRetrievalId = "00000000-0000-0000-0000-000000000000";
    const [ragRows, retrievalRows] = await scoped(workspaceId).batch([
      db.select({ id: ragTraces.id })
        .from(ragTraces)
        .where(and(
          eq(ragTraces.id, traceCandidates.ragTraceId ?? ""),
          eq(ragTraces.assistantMessageId, message.id),
          eq(ragTraces.userId, userId),
          eq(ragTraces.workspaceId, workspaceId),
        ))
        .orderBy(desc(ragTraces.createdAt))
        .limit(1) as never,
      db.select({ id: retrievalTraces.id })
        .from(retrievalTraces)
        .where(and(
          eq(retrievalTraces.id, traceCandidates.retrievalTraceId ?? emptyRetrievalId),
          eq(retrievalTraces.workspaceId, workspaceId),
        ))
        .orderBy(desc(retrievalTraces.createdAt))
        .limit(1) as never,
    ]) as unknown as [Array<{ id: string }>, Array<{ id: string }>];

    const [insertedRows] = await scoped(workspaceId).batch([
      db.insert(answerFeedback).values({
        workspaceId,
        chatId: message.chatId,
        messageId: message.id,
        ragTraceId: ragRows?.[0]?.id ?? null,
        retrievalTraceId: retrievalRows?.[0]?.id ?? null,
        userId,
        rating: parsed.data.rating,
        reasons: parsed.data.reasons ?? [],
        comment: parsed.data.comment?.trim() || null,
      }).returning({ id: answerFeedback.id }) as never,
    ]) as unknown as [Array<{ id: number }>];

    if (!insertedRows?.[0]) return NextResponse.json({ error: "Answer not found" }, { status: 404 });
    return NextResponse.json({ ok: true });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    return internalError("answer-feedback", error);
  }
}

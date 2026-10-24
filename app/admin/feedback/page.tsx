import { desc, eq } from "drizzle-orm";
import { notFound } from "next/navigation";
import { FeedbackDashboard, type FeedbackRow } from "@/app/admin/feedback/feedback-dashboard";
import { isAdmin } from "@/lib/auth";
import { db } from "@/lib/db-config";
import { answerFeedback, chatMessages } from "@/lib/db-schema";

export const dynamic = "force-dynamic";

export default async function AdminFeedbackPage() {
  if (!(await isAdmin())) notFound();

  const rows = await db
    .select({
      id: answerFeedback.id,
      messageId: answerFeedback.messageId,
      rating: answerFeedback.rating,
      reasons: answerFeedback.reasons,
      comment: answerFeedback.comment,
      userId: answerFeedback.userId,
      createdAt: answerFeedback.createdAt,
      ragTraceId: answerFeedback.ragTraceId,
      retrievalTraceId: answerFeedback.retrievalTraceId,
      answer: chatMessages.content,
      messageParts: chatMessages.parts,
    })
    .from(answerFeedback)
    .innerJoin(chatMessages, eq(answerFeedback.messageId, chatMessages.id))
    .orderBy(desc(answerFeedback.createdAt))
    .limit(100);

  const feedback: FeedbackRow[] = rows.map((row) => ({
    id: row.id,
    messageId: row.messageId,
    rating: row.rating,
    reasons: row.reasons ?? [],
    comment: row.comment,
    userId: row.userId,
    createdAt: row.createdAt.toISOString(),
    ragTraceId: row.ragTraceId,
    retrievalTraceId: row.retrievalTraceId,
    answer: row.answer,
    messageParts: Array.isArray(row.messageParts) ? row.messageParts : [],
  }));

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-7xl px-6 py-10">
        <h1 className="font-serif text-4xl">Answer Feedback</h1>
        <p className="mt-2 text-sm text-muted-foreground">Review recent ratings and inspect their stored retrieval evidence.</p>
        <FeedbackDashboard rows={feedback} />
      </div>
    </main>
  );
}

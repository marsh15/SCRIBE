"use client";

import { useState } from "react";
import type { UIMessage } from "@ai-sdk/react";
import { RAGInspector } from "@/components/rag-inspector";

export type FeedbackRow = {
  id: number;
  messageId: string;
  rating: string;
  reasons: string[];
  comment: string | null;
  userId: string;
  createdAt: string;
  ragTraceId: string | null;
  retrievalTraceId: string | null;
  answer: string;
  messageParts: unknown[];
};

function reasonLabel(reason: string) {
  return reason.replaceAll("_", " ");
}

export function FeedbackDashboard({ rows }: { rows: FeedbackRow[] }) {
  const [selectedId, setSelectedId] = useState<number | null>(rows[0]?.id ?? null);
  const selected = rows.find((row) => row.id === selectedId) ?? null;
  const selectedMessage = selected
    ? ({ id: selected.messageId, role: "assistant", parts: selected.messageParts } as UIMessage)
    : null;

  return (
    <div className="mt-8 grid min-h-[34rem] gap-5 lg:grid-cols-[minmax(18rem,0.9fr)_minmax(0,1.1fr)]">
      <section aria-label="Feedback entries" className="space-y-2">
        {rows.length === 0 ? (
          <p className="rounded-sm border border-border bg-card p-5 text-sm text-muted-foreground">No answer feedback has been submitted.</p>
        ) : rows.map((row) => (
          <button
            key={row.id}
            type="button"
            aria-pressed={selectedId === row.id}
            onClick={() => setSelectedId(row.id)}
            className={`block w-full rounded-sm border p-4 text-left transition-colors ${selectedId === row.id ? "border-rag/50 bg-rag/5" : "border-border bg-card hover:border-rag/30"}`}
          >
            <span className="flex items-center justify-between gap-3">
              <span className={`font-mono text-[10px] uppercase tracking-wider ${row.rating === "down" ? "text-destructive" : "text-rag"}`}>
                {row.rating === "down" ? "Thumbs down" : "Thumbs up"}
              </span>
              <time className="text-[10px] text-muted-foreground" dateTime={row.createdAt}>{new Date(row.createdAt).toLocaleString()}</time>
            </span>
            <span className="mt-2 block line-clamp-2 text-sm text-foreground">{row.answer || "(answer text unavailable)"}</span>
            {row.reasons.length > 0 && (
              <span className="mt-2 block text-[10px] capitalize text-muted-foreground">{row.reasons.map(reasonLabel).join(" · ")}</span>
            )}
            {row.comment && <span className="mt-2 block line-clamp-2 text-xs text-muted-foreground">{row.comment}</span>}
          </button>
        ))}
      </section>

      {selected && selectedMessage ? (
        <section aria-label="Selected answer evidence" className="min-h-[34rem] overflow-hidden rounded-sm border border-border bg-card">
          <div className="border-b border-border p-4">
            <h2 className="font-serif text-xl">Retrieval timeline</h2>
            <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">
              {selected.retrievalTraceId ? `Retrieval trace ${selected.retrievalTraceId}` : selected.ragTraceId ? `RAG trace ${selected.ragTraceId}` : "No trace link was stored for this answer."}
            </p>
            <p className="mt-3 text-sm leading-relaxed">{selected.answer || "(answer text unavailable)"}</p>
          </div>
          <div className="h-[28rem] min-h-0">
            <RAGInspector messages={[selectedMessage]} status="ready" />
          </div>
        </section>
      ) : (
        <div className="flex min-h-[20rem] items-center justify-center rounded-sm border border-dashed border-border text-sm text-muted-foreground">
          Select feedback to inspect its stored evidence.
        </div>
      )}
    </div>
  );
}

"use client";

import { useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";

const REASONS = [
  ["wrong_citation", "Wrong citation"],
  ["missing_info", "Missing information"],
  ["outdated_version", "Outdated version"],
  ["should_abstain", "Should have abstained"],
] as const;

type Rating = "up" | "down";
type Reason = (typeof REASONS)[number][0];

export function AnswerFeedback({ messageId }: { messageId: string }) {
  const [rating, setRating] = useState<Rating | null>(null);
  const [reasons, setReasons] = useState<Reason[]>([]);
  const [comment, setComment] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");

  const toggleReason = (reason: Reason) => {
    setReasons((current) => current.includes(reason)
      ? current.filter((item) => item !== reason)
      : [...current, reason]);
  };

  const submit = async () => {
    if (!rating || state === "sending" || state === "sent") return;
    setState("sending");
    try {
      const response = await fetch("/api/feedback", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          messageId,
          rating,
          ...(reasons.length ? { reasons } : {}),
          ...(comment.trim() ? { comment: comment.trim() } : {}),
        }),
      });
      if (!response.ok) throw new Error("feedback unavailable");
      setState("sent");
    } catch {
      setState("error");
    }
  };

  if (state === "sent") {
    return <p className="mt-4 text-xs text-muted-foreground" role="status">Thanks for your feedback.</p>;
  }

  return (
    <section className="mt-4 border-t border-border/50 pt-3" aria-label="Answer feedback">
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-muted-foreground">Was this answer helpful?</span>
        <button
          type="button"
          aria-label="Helpful answer"
          aria-pressed={rating === "up"}
          disabled={state === "sending"}
          onClick={() => { setRating("up"); setState("idle"); }}
          className={`rounded-sm border p-2 transition-colors ${rating === "up" ? "border-rag/50 bg-rag/10 text-rag" : "border-border text-muted-foreground hover:text-foreground"}`}
        >
          <ThumbsUp className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label="Unhelpful answer"
          aria-pressed={rating === "down"}
          disabled={state === "sending"}
          onClick={() => { setRating("down"); setState("idle"); }}
          className={`rounded-sm border p-2 transition-colors ${rating === "down" ? "border-destructive/50 bg-destructive/10 text-destructive" : "border-border text-muted-foreground hover:text-foreground"}`}
        >
          <ThumbsDown className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>

      {rating && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-2" aria-label="Feedback reasons">
            {REASONS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={reasons.includes(value)}
                onClick={() => toggleReason(value)}
                className={`rounded-sm border px-2.5 py-1.5 text-[11px] transition-colors ${reasons.includes(value) ? "border-rag/50 bg-rag/10 text-rag" : "border-border text-muted-foreground hover:text-foreground"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="block text-[11px] text-muted-foreground">
            Optional comment
            <textarea
              value={comment}
              maxLength={2000}
              onChange={(event) => setComment(event.target.value)}
              rows={3}
              className="mt-1 block w-full resize-y rounded-sm border border-border bg-background px-3 py-2 text-xs text-foreground outline-none focus:border-rag/50"
            />
            <span className="mt-1 block text-right tabular-nums">{comment.length}/2000</span>
          </label>
          {state === "error" && <p role="alert" className="text-xs text-destructive">Feedback could not be saved. Please try again.</p>}
          <button
            type="button"
            onClick={() => void submit()}
            disabled={state === "sending"}
            className="min-h-10 rounded-sm bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-60"
          >
            {state === "sending" ? "Sending…" : "Send feedback"}
          </button>
        </div>
      )}
    </section>
  );
}

// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("@/components/rag-inspector", () => ({
  RAGInspector: ({ messages, status, chatId }: { messages: Array<{ id: string }>; status: string; chatId?: string }) => (
    <div data-testid="timeline" data-message-id={messages[0]?.id} data-status={status} data-chat-id={chatId ?? "omitted"} />
  ),
}));

import { FeedbackDashboard, type FeedbackRow } from "@/app/admin/feedback/feedback-dashboard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const row = (id: number, messageId: string): FeedbackRow => ({
  id, messageId, rating: "down", reasons: ["wrong_citation"], comment: "Review this source.", userId: "user-1",
  createdAt: "2026-10-01T00:00:00.000Z", ragTraceId: null, retrievalTraceId: `trace-${id}`,
  answer: `Answer ${id}`, messageParts: [{ type: "tool-invocation", result: { type: "rag_search_result" } }],
});

it("selects the stored assistant message for the trace inspector without a chatId", async () => {
  await act(async () => root.render(<FeedbackDashboard rows={[row(2, "answer-2"), row(1, "answer-1")]} />));
  expect(host.querySelector('[data-testid="timeline"]')?.getAttribute("data-message-id")).toBe("answer-2");
  expect(host.querySelector('[data-testid="timeline"]')?.getAttribute("data-chat-id")).toBe("omitted");
  const older = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("Answer 1"))!;
  await act(async () => older.click());
  expect(host.querySelector('[data-testid="timeline"]')?.getAttribute("data-message-id")).toBe("answer-1");
  expect(host.querySelector('[data-testid="timeline"]')?.getAttribute("data-status")).toBe("ready");
});

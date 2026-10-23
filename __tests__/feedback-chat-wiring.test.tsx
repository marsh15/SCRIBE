// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  messages: [] as Array<{ id: string; role: "assistant"; parts: unknown[] }>,
  messageReads: 0,
  getChatMessages: vi.fn(async () => [] as Array<{ id: string; chatId: string; role: string; content: string; parts: unknown[]; createdAt: Date }>),
}));

vi.mock("@ai-sdk/react", () => ({
  useChat: () => ({ messages: h.messages, status: "ready", sendMessage: vi.fn(), error: null, clearError: vi.fn() }),
}));
vi.mock("@/components/chat-context", () => ({ useChatState: () => ({ setMessages: vi.fn(), setStatus: vi.fn() }) }));
vi.mock("@/app/chat/actions", () => ({ getChatMessages: h.getChatMessages, getChatVerifications: vi.fn(async () => ({})) }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "chat-1" }), useSearchParams: () => new URLSearchParams() }));
vi.mock("ai", () => ({ DefaultChatTransport: class { constructor(_options: unknown) {} } }));
vi.mock("@/components/cited-markdown", () => ({ CitedMarkdown: ({ text }: { text: string }) => <div>{text}</div> }));

import DynamicRagChatbot from "@/app/chat/[id]/page";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  h.messageReads = 0;
  h.messages = [
    { id: "interleaved-history-id", role: "assistant", parts: [{ type: "text", text: "An answer from another tab." }] },
    { id: "stable-stream-answer-id", role: "assistant", parts: [{ type: "text", text: "The answer is 42." }] },
  ];
  h.getChatMessages.mockClear();
  h.getChatMessages.mockImplementation(async () => {
    h.messageReads += 1;
    return [{
      id: "interleaved-history-id", chatId: "chat-1", role: "assistant", content: "An answer from another tab.",
      parts: [{ type: "text", text: "An answer from another tab." }], createdAt: new Date("2026-10-01T00:00:00Z"),
    }];
  });
  window.history.replaceState(null, "", "/chat/chat-1");
  window.matchMedia = vi.fn(() => ({ matches: false })) as unknown as typeof window.matchMedia;
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })));
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it("posts the stable stream id despite unrelated history interleaved before it", async () => {
  await act(async () => {
    root.render(<DynamicRagChatbot />);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  expect(host.textContent).toContain("The answer is 42.");
  const feedbackSections = host.querySelectorAll('[aria-label="Answer feedback"]');
  expect(feedbackSections).toHaveLength(2);
  const currentAnswerFeedback = feedbackSections[1];
  await act(async () => currentAnswerFeedback.querySelector<HTMLButtonElement>('button[aria-label="Helpful answer"]')!.click());
  await act(async () => [...currentAnswerFeedback.querySelectorAll("button")].find((button) => button.textContent === "Send feedback")!.click());

  const fetchMock = vi.mocked(fetch);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
    messageId: "stable-stream-answer-id",
    rating: "up",
  });
});

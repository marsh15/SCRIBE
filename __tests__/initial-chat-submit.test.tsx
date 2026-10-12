// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const chatMocks = vi.hoisted(() => ({
  sendMessage: vi.fn(() => new Promise<void>(() => {})),
  getChatMessages: vi.fn(async () => []),
}));

vi.mock("@ai-sdk/react", () => ({
  useChat: () => ({
    messages: [],
    status: "ready",
    sendMessage: chatMocks.sendMessage,
    error: null,
    clearError: vi.fn(),
  }),
}));

vi.mock("@/components/chat-context", () => ({
  useChatState: () => ({ setMessages: vi.fn(), setStatus: vi.fn() }),
}));

vi.mock("@/app/chat/actions", () => ({
  getChatMessages: chatMocks.getChatMessages,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "chat-1" }),
  useSearchParams: () => new URLSearchParams(window.location.search),
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock("ai", () => ({
  DefaultChatTransport: class {
    constructor(_options: unknown) {}
  },
}));

vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

import DynamicRagChatbot from "@/app/chat/[id]/page";

beforeEach(() => {
  chatMocks.sendMessage.mockClear();
  chatMocks.getChatMessages.mockClear();
  window.history.replaceState(null, "", "/chat/chat-1?q=hello");
  window.matchMedia = vi.fn(() => ({ matches: false })) as unknown as typeof window.matchMedia;
  Element.prototype.scrollIntoView = vi.fn();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
});

afterEach(() => {
  document.body.innerHTML = "";
});

it("sends the first prompt once when the chat remounts during streaming", async () => {
  const firstContainer = document.createElement("div");
  document.body.append(firstContainer);
  const firstRoot = createRoot(firstContainer);

  await act(async () => {
    firstRoot.render(<DynamicRagChatbot />);
  });
  expect(chatMocks.sendMessage).toHaveBeenCalledTimes(1);
  expect(window.location.search).toBe("");

  await act(async () => {
    firstRoot.unmount();
  });

  const secondContainer = document.createElement("div");
  document.body.append(secondContainer);
  const secondRoot = createRoot(secondContainer);

  await act(async () => {
    secondRoot.render(<DynamicRagChatbot />);
  });

  expect(chatMocks.sendMessage).toHaveBeenCalledTimes(1);

  await act(async () => {
    secondRoot.unmount();
  });
});

it("sends one message for one Enter keypress in an existing chat", async () => {
  window.history.replaceState(null, "", "/chat/chat-1");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(<DynamicRagChatbot />);
  });

  const textarea = container.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "hello");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });

  const enter = new KeyboardEvent("keydown", {
    key: "Enter",
    bubbles: true,
    cancelable: true,
  });
  await act(async () => {
    textarea.dispatchEvent(enter);
  });

  expect(enter.defaultPrevented).toBe(true);
  expect(chatMocks.sendMessage).toHaveBeenCalledTimes(1);

  await act(async () => {
    root.unmount();
  });
});

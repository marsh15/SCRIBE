import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  engine: "python" as "python" | "legacy",
  streamText: vi.fn(),
  options: null as Record<string, unknown> | null,
  pythonStreamOptions: null as Record<string, unknown> | null,
  legacyStreamOptions: null as Record<string, unknown> | null,
  inserts: [] as Array<Record<string, unknown>>,
}));

vi.mock("ai", async () => {
  const actual = await vi.importActual<typeof import("ai")>("ai");
  return {
    ...actual,
    streamText: h.streamText,
    convertToModelMessages: async () => [],
    tool: (value: unknown) => value,
    stepCountIs: () => () => true,
  };
});
vi.mock("@ai-sdk/google", () => ({ google: () => ({ model: "fake-model" }) }));
vi.mock("@supermemory/tools/ai-sdk", () => ({ withSupermemory: () => ({ model: "fake-supermemory-model" }) }));
vi.mock("@/lib/db-config", () => ({
  db: {
    query: { chats: { findFirst: vi.fn(async () => ({ id: "chat-1" })) } },
    insert: () => ({ values: async (value: Record<string, unknown>) => { h.inserts.push(value); return []; } }),
  },
}));
vi.mock("@/lib/workspace", () => ({
  resolveWorkspace: async () => ({ userId: "user-1", workspaceId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }),
}));
vi.mock("@/lib/auth", () => ({ isNotAuthenticatedError: () => false }));
vi.mock("@/lib/billing/usage", () => ({ getUsageSummary: async () => ({ allowOverage: true }), recordUsageEvent: vi.fn() }));
vi.mock("@/lib/rag-observability", () => ({
  buildRagToolResult: vi.fn(),
  completeRagTrace: vi.fn(),
  recordRagEvaluation: vi.fn(),
  recordRagTrace: vi.fn(),
}));
vi.mock("@/lib/rag-eval", () => ({ evaluateRagAnswer: vi.fn() }));
vi.mock("@/lib/search", () => ({ searchDocuments: vi.fn() }));
vi.mock("@/lib/python-retrieval", () => ({
  createTurnRetriever: () => vi.fn(),
  getRetrievalEngine: () => h.engine,
  modelOutputForSearch: (value: unknown) => value,
  shouldUseSupermemory: () => false,
  SEARCH_UNAVAILABLE_MESSAGE: "Search unavailable",
}));
vi.mock("@/lib/chat-answer", () => ({ ensureStructuredAnswer: (value: string) => value }));
vi.mock("@/lib/chat-prompt", () => ({ buildSystemPrompt: () => "test system prompt" }));
vi.mock("@/lib/evidence/verification", () => ({
  VERIFICATION_PART: "data-verification",
  VERIFICATION_PART_ID: "verification",
  requestVerification: async () => ({ state: "done" }),
}));

import { createUIMessageStreamResponse } from "ai";
import { POST } from "@/app/api/chat/route";

function modelStream(messageId: string, includeFinish: boolean) {
  return new ReadableStream({
    start(controller) {
      controller.enqueue({ type: "start", messageId });
      controller.enqueue({ type: "text-start", id: "text-1" });
      controller.enqueue({ type: "text-delta", id: "text-1", delta: "Answer text" });
      controller.enqueue({ type: "text-end", id: "text-1" });
      if (includeFinish) controller.enqueue({ type: "finish" });
      controller.close();
    },
  });
}

beforeEach(() => {
  h.engine = "python";
  h.options = null;
  h.pythonStreamOptions = null;
  h.legacyStreamOptions = null;
  h.inserts.length = 0;
  h.streamText.mockReset();
  h.streamText.mockImplementation((options: Record<string, unknown>) => {
    h.options = options;
    return {
      toUIMessageStream(streamOptions: Record<string, unknown>) {
        h.pythonStreamOptions = streamOptions;
        const generateMessageId = streamOptions.generateMessageId as () => string;
        return modelStream(generateMessageId(), false);
      },
      toUIMessageStreamResponse(streamOptions: Record<string, unknown>) {
        h.legacyStreamOptions = streamOptions;
        const generateMessageId = streamOptions.generateMessageId as () => string;
        return createUIMessageStreamResponse({ stream: modelStream(generateMessageId(), true) });
      },
    };
  });
  vi.stubEnv("GOOGLE_GENERATIVE_AI_API_KEY", "test-provider-key");
});

afterEach(() => vi.unstubAllEnvs());

describe("chat assistant message IDs", () => {
  it.each(["python", "legacy"] as const)("uses the streamed ID for the persisted answer with %s retrieval", async (engine) => {
    h.engine = engine;
    const response = await POST(new Request("https://scribe.example/api/chat?chatId=chat-1", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: [{ id: "user-message", role: "user", parts: [{ type: "text", text: "Question" }] }] }),
    }));
    expect(response.status).toBe(200);

    const options = h.options!;
    const onFinish = options.onFinish as (args: unknown) => Promise<void>;
    await onFinish({ response: { messages: [] }, text: "Answer text", usage: {} });
    const streamOptions = engine === "python" ? h.pythonStreamOptions! : h.legacyStreamOptions!;
    const streamedId = (streamOptions.generateMessageId as () => string)();
    const persisted = h.inserts.findLast((value) => value.role === "assistant");
    expect(persisted?.id).toBe(streamedId);

    const body = await response.text();
    expect(body).toContain(`"type":"start","messageId":"${streamedId}"`);
  });
});

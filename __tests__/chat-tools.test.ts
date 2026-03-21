import type { UIMessage } from "@ai-sdk/react";
import { describe, expect, it } from "vitest";
import {
  countKnowledgeBaseInvocations,
  getLastKnowledgeBaseInvocation,
  getToolInvocations,
  messageUsesKnowledgeBase,
} from "@/lib/chat-tools";

describe("chat-tools", () => {
  it("reads legacy tool-invocation parts", () => {
    const message = {
      id: "assistant-1",
      role: "assistant",
      parts: [
        {
          type: "tool-invocation",
          toolName: "searchKnowledgeBase",
          toolCallId: "call-1",
          args: { query: "contract" },
          result: "Chunk A",
        },
      ],
    } as unknown as UIMessage;

    expect(getToolInvocations(message)).toEqual([
      expect.objectContaining({
        toolName: "searchKnowledgeBase",
        toolCallId: "call-1",
        result: "Chunk A",
      }),
    ]);
    expect(messageUsesKnowledgeBase(message)).toBe(true);
  });

  it("reads typed AI SDK tool parts and outputs", () => {
    const messages = [
      {
        id: "assistant-1",
        role: "assistant",
        parts: [
          {
            type: "tool-searchKnowledgeBase",
            toolCallId: "call-2",
            input: { query: "refund policy" },
            output: "Chunk B",
            state: "output-available",
          },
        ],
      },
      {
        id: "assistant-2",
        role: "assistant",
        parts: [
          {
            type: "tool-searchKnowledgeBase",
            toolCallId: "call-3",
            input: { query: "deadlines" },
            state: "input-available",
          },
        ],
      },
    ] as unknown as UIMessage[];

    expect(countKnowledgeBaseInvocations(messages)).toBe(2);
    expect(getLastKnowledgeBaseInvocation(messages)).toEqual(
      expect.objectContaining({
        toolName: "searchKnowledgeBase",
        toolCallId: "call-3",
        result: undefined,
      }),
    );
  });
});

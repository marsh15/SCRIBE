import type { UIMessage } from "@ai-sdk/react";
import { describe, expect, it } from "vitest";
import {
  countKnowledgeBaseInvocations,
  getLastKnowledgeBaseInvocation,
  getToolInvocations,
  messageUsesKnowledgeBase,
} from "@/lib/chat-tools";
import { extractRagToolResult, RAG_TOOL_RESULT_TYPE } from "@/lib/rag-types";

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

  it("preserves structured RAG tool results for the inspector", () => {
    const message = {
      id: "assistant-structured",
      role: "assistant",
      parts: [
        {
          type: "tool-searchKnowledgeBase",
          toolCallId: "call-structured",
          input: { query: "renewal terms" },
          output: {
            type: RAG_TOOL_RESULT_TYPE,
            traceId: "trace-1",
            query: "renewal terms",
            status: "retrieved",
            context: "[Citation 1] Source: [terms.pdf](/files/7)\nContent: Renewal text",
            timings: { embeddingMs: 12, retrievalMs: 8, totalMs: 20 },
            topK: 10,
            threshold: 0.3,
            chunks: [
              {
                rank: 1,
                documentId: 99,
                fileId: 7,
                fileName: "terms.pdf",
                fileType: "application/pdf",
                chunkIndex: 3,
                similarity: 0.82,
                metadata: { estimatedPage: 4 },
                contentPreview: "Renewal text",
                content: "Renewal text",
              },
            ],
          },
          state: "output-available",
        },
      ],
    } as unknown as UIMessage;

    const invocation = getLastKnowledgeBaseInvocation([message]);
    const structured = extractRagToolResult(invocation?.result);

    expect(structured?.traceId).toBe("trace-1");
    expect(structured?.chunks[0]).toEqual(
      expect.objectContaining({
        fileName: "terms.pdf",
        similarity: 0.82,
      }),
    );
  });
});

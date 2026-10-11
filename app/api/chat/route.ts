import {
  streamText,
  type UIMessage,
  convertToModelMessages,
  tool,
  stepCountIs,
} from "ai";
import { NextResponse } from "next/server";
import { google } from "@ai-sdk/google";
import { withSupermemory } from "@supermemory/tools/ai-sdk";
import { z } from "zod";
import { nanoid } from "nanoid";
import { searchDocuments } from "@/lib/search";
import { resolveWorkspace } from "@/lib/workspace";
import { getUsageSummary, recordUsageEvent } from "@/lib/billing/usage";
import {
  buildRagToolResult,
  completeRagTrace,
  recordRagEvaluation,
  recordRagTrace,
} from "@/lib/rag-observability";
import { evaluateRagAnswer } from "@/lib/rag-eval";
import {
  createTurnRetriever,
  getRetrievalEngine,
  modelOutputForSearch,
  shouldUseSupermemory,
  SEARCH_UNAVAILABLE_MESSAGE,
} from "@/lib/python-retrieval";
import { ensureStructuredAnswer } from "@/lib/chat-answer";
import { buildSystemPrompt } from "@/lib/chat-prompt";
import { extractRagToolResult, type RagToolResult } from "@/lib/rag-types";

export type ChatMessage = UIMessage;

const CHAT_MODEL_ID = process.env.GOOGLE_CHAT_MODEL || "gemini-2.5-flash";
const SUPERMEMORY_ENABLED = process.env.ENABLE_SUPERMEMORY === "true";

type TextPart = {
  type: "text";
  text: string;
};

type GenericPart = {
  type?: string;
  text?: string;
};

type UsageLike = {
  inputTokens?: number;
  outputTokens?: number;
};

type ToolCallLike = {
  type?: string;
  toolName?: string;
  args?: unknown;
  toolCallId?: string;
};

type ToolResultLike = {
  type?: string;
  toolCallId?: string;
  output?: unknown;
  result?: unknown;
};

type ResponseLike = {
  messages?: Array<{
    content?: unknown;
  }>;
};

function sanitizeMessages(messages: ChatMessage[]) {
  return messages.map((message) => {
    const cleanedParts = (message.parts ?? []).filter(
      (part): part is TextPart => (part as GenericPart).type === "text"
    );

    return {
      ...message,
      parts: cleanedParts,
    };
  });
}

function extractTextFromMessage(message: ChatMessage) {
  const parts = (message.parts ?? []) as GenericPart[];
  const textFromParts = parts
    .filter((part) => part.type === "text")
    .map((part) => part.text ?? "")
    .join("");

  if (textFromParts.trim().length > 0) return textFromParts;
  return "";
}

function getErrorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;

  try {
    return JSON.stringify(error);
  } catch {
    return "Unknown error";
  }
}

function formatChatError(error: unknown) {
  const message = getErrorMessage(error);

  if (/api[_\s-]?key|API_KEY_INVALID|unauthorized|authentication/i.test(message)) {
    return "Gemini is not responding because the configured Google Generative AI API key is missing or invalid. Add a valid GOOGLE_GENERATIVE_AI_API_KEY and retry.";
  }

  if (/quota|rate.?limit|429/i.test(message)) {
    return "Gemini is temporarily unavailable because the API quota or rate limit was reached. Please retry after the provider limit resets.";
  }

  if (/model.*not.*found|not found.*model|404/i.test(message)) {
    return `Gemini model ${CHAT_MODEL_ID} is not available for this API key. Set GOOGLE_CHAT_MODEL to an enabled Gemini model and retry.`;
  }

  return "The AI provider failed before a response could be completed. Please retry after checking the server logs.";
}

export async function POST(req: Request) {
  const requestStart = Date.now();
  try {
    const { userId, workspaceId } = await resolveWorkspace();
    const usageSummary = await getUsageSummary(userId);
    if (!usageSummary.allowOverage && usageSummary.projectedOverageInr > 0) {
      return NextResponse.json(
        {
          error: "Free plan usage limit reached.",
          message: "You've used all your free quota for this billing period.",
          upgradeUrl: "/pricing",
        },
        { status: 402 }
      );
    }
    if (!process.env.GOOGLE_GENERATIVE_AI_API_KEY?.trim()) {
      return new Response(
        "Gemini is not configured. Set GOOGLE_GENERATIVE_AI_API_KEY and retry.",
        { status: 503 }
      );
    }

    const { messages }: { messages: ChatMessage[] } = await req.json();
    if (!Array.isArray(messages) || messages.length === 0) {
      return new Response("No chat messages were provided.", { status: 400 });
    }

    const url = new URL(req.url);
    const chatId = url.searchParams.get("chatId") || undefined;
    if (chatId) {
      const { db: ownershipDb } = await import("@/lib/db-config");
      const { chats: chatsTable } = await import("@/lib/db-schema");
      const { and, eq } = await import("drizzle-orm");
      const ownedChat = await ownershipDb.query.chats.findFirst({
        where: and(
          eq(chatsTable.id, chatId),
          eq(chatsTable.userId, userId),
          eq(chatsTable.workspaceId, workspaceId)
        ),
        columns: { id: true },
      });
      if (!ownedChat) {
        return new Response("Chat not found.", { status: 404 });
      }
    }
    const lastUserMessage = messages[messages.length - 1];
    const userMessageId = lastUserMessage.id || nanoid();

    const sanitizedMessages = sanitizeMessages(messages);
    const baseModel = google(CHAT_MODEL_ID);
    const retrievalEngine = getRetrievalEngine();
    const model = shouldUseSupermemory(retrievalEngine, SUPERMEMORY_ENABLED)
      ? withSupermemory(baseModel, userId, { conversationId: chatId ?? userId })
      : baseModel;

    // Save user message to DB immediately — BEFORE starting the stream.
    // If we save only in onFinish and Gemini crashes mid-stream, the message is lost forever.
    if (chatId) {
      const { db: dbInstance } = await import("@/lib/db-config");
      const { chatMessages: chatMessagesTable } = await import("@/lib/db-schema");
      try {
        await dbInstance.insert(chatMessagesTable).values({
          id: userMessageId,
          chatId,
          role: lastUserMessage.role,
          content: extractTextFromMessage(lastUserMessage),
          parts: (lastUserMessage.parts ?? []) as never,
        });
      } catch (e) {
        // Non-fatal: message may already exist (idempotent retry)
        console.error("[Chat] Failed to pre-save user message:", e);
      }
    }

    const ragToolResults: RagToolResult[] = [];
    // One retriever per turn: E-labels are unique across every search of this turn (D14).
    const retrieveForTurn = createTurnRetriever();

    const userTools = {
      searchKnowledgeBase: tool({
        description: "Search the knowledge base for relevant information",
        inputSchema: z.object({
          query: z.string().describe("The search query to find relevant documents"),
        }),
        // INV-13: the model receives only the labelled, delimited context of a Python search, never raw chunks.
        toModelOutput: ({ output }) =>
          modelOutputForSearch(output) ?? { type: "json", value: output as never },
        execute: async ({ query }) => {
          if (retrievalEngine === "python") {
            // workspace comes from resolveWorkspace(), never from the request body (INV-15)
            const pyResult = await retrieveForTurn({ query, userId, workspaceId });
            ragToolResults.push(pyResult);
            return pyResult;
          }
          try {
            const search = await searchDocuments(query, userId, workspaceId, 10, 0.3);
            let toolResult = buildRagToolResult(search);

            if (chatId) {
              try {
                toolResult = await recordRagTrace({
                  chatId,
                  userId,
                  workspaceId,
                  userMessageId,
                  search,
                });
              } catch (traceError) {
                console.error("[RAG] Failed to record retrieval trace:", traceError);
              }
            }

            ragToolResults.push(toolResult);
            return toolResult;
          } catch (error) {
            console.error("Search error:", error);
            return {
              type: "rag_search_result",
              query,
              status: "failed",
              message: "Error searching the knowledge base.",
              context: "Error searching the knowledge base.",
              timings: { embeddingMs: 0, retrievalMs: 0, totalMs: 0 },
              topK: 10,
              threshold: 0.3,
              chunks: [],
            };
          }
        },
      }),
    };

    const result = streamText({
      model,
      messages: await convertToModelMessages(sanitizedMessages),
      tools: userTools,
      system: buildSystemPrompt(retrievalEngine),
      stopWhen: stepCountIs(3),
      onFinish: async ({ response, text, usage }: { response?: ResponseLike; text?: string; usage?: UsageLike }) => {
        if (!chatId) return;

        const { db } = await import("@/lib/db-config");
        const { chatMessages } = await import("@/lib/db-schema");

        try {
          // User message was already saved before the stream started — only save assistant response here
          const assistantParts: Array<{
            type: string;
            text?: string;
            toolName?: string;
            args?: unknown;
            toolCallId?: string;
            result?: unknown;
          }> = [];

          const responseContent = (response?.messages ?? []).flatMap((message) => {
            const content = message.content;
            if (!Array.isArray(content)) return [] as Array<ToolCallLike | ToolResultLike>;
            return content.filter(
              (item): item is ToolCallLike | ToolResultLike =>
                typeof item === "object" &&
                item !== null &&
                "type" in item &&
                ((item as ToolCallLike).type === "tool-call" ||
                  (item as ToolResultLike).type === "tool-result"),
            );
          });

          const toolCalls = responseContent.filter(
            (item): item is ToolCallLike => item.type === "tool-call",
          );

          const toolResults = new Map(
            responseContent
              .filter((item): item is ToolResultLike => item.type === "tool-result")
              .map((item) => [
                item.toolCallId ?? "",
                item.output ?? item.result,
              ]),
          );

          const ragResultsFromResponse = Array.from(toolResults.values())
            .map(extractRagToolResult)
            .filter((result): result is RagToolResult => result !== null);
          const completedRagResults =
            ragResultsFromResponse.length > 0 ? ragResultsFromResponse : ragToolResults;
          const searchUnavailable = completedRagResults.some(
            (result) => result.status === "unavailable",
          );
          // INV-16 / B4: never persist an answer that has no evidence behind it.
          const finalText = searchUnavailable
            ? SEARCH_UNAVAILABLE_MESSAGE
            : ensureStructuredAnswer(text ?? "");

          if (finalText) {
            assistantParts.push({ type: "text", text: finalText });
          }

          for (const call of toolCalls) {
            assistantParts.push({
              type: "tool-invocation",
              toolName: call.toolName,
              args: call.args,
              toolCallId: call.toolCallId,
              result: call.toolCallId ? toolResults.get(call.toolCallId) : undefined,
            });
          }

          const assistantMessageId = nanoid();
          await db.insert(chatMessages).values({
            id: assistantMessageId,
            chatId,
            role: "assistant",
            content: finalText,
            parts: assistantParts as never,
          });

          const totalRequestMs = Math.max(0, Date.now() - requestStart);
          for (const ragResult of completedRagResults) {
            // Python traces live in retrieval_traces, written by the Python service.
            if (!ragResult.traceId || ragResult.engine === "python") continue;

            try {
              const generationMs = Math.max(
                0,
                totalRequestMs - ragResult.timings.totalMs
              );
              await completeRagTrace({
                traceId: ragResult.traceId,
                assistantMessageId,
                generationMs,
                totalMs: totalRequestMs,
              });

              const evaluation = await evaluateRagAnswer({
                traceId: ragResult.traceId,
                question: ragResult.query,
                answer: finalText,
                chunks: ragResult.chunks,
              });
              await recordRagEvaluation(evaluation);
            } catch (traceError) {
              console.error("[RAG] Failed to complete trace or evaluation:", traceError);
            }
          }

          if (usage?.inputTokens && usage.inputTokens > 0) {
            await recordUsageEvent({
              userId,
              metric: "model_input_tokens",
              quantity: usage.inputTokens,
              unit: "tokens",
              sourceType: "chat",
              sourceId: chatId,
              isEstimated: false,
            });
          }

          if (usage?.outputTokens && usage.outputTokens > 0) {
            await recordUsageEvent({
              userId,
              metric: "model_output_tokens",
              quantity: usage.outputTokens,
              unit: "tokens",
              sourceType: "chat",
              sourceId: chatId,
              isEstimated: false,
            });
          }
        } catch (error) {
          console.error("Failed to save assistant message or usage in onFinish", error);
        }

      },
    });

    return result.toUIMessageStreamResponse({
      onError: (error) => {
        const formatted = formatChatError(error);
        console.error("[Chat] Stream failed:", error);
        return formatted;
      },
    });
  } catch (error) {
    console.error("Error streaming chat completion:", error);
    return new Response(formatChatError(error), { status: 500 });
  }
}

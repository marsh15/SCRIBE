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
import { searchDocuments } from "@/lib/search";
import { getUserId } from "@/lib/auth";
import { getUsageSummary, recordUsageEvent } from "@/lib/billing/usage";

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

function ensureStructuredAnswer(text: string, hadKnowledgeLookup: boolean) {
  const base = text?.trim() ?? "";
  if (!base) return base;

  const hasAnswerHeader = /(^|\n)#{0,3}\s*Answer\b/i.test(base);
  const hasKeyPointsHeader = /(^|\n)#{0,3}\s*Key Points\b/i.test(base);
  const hasSourcesHeader = /(^|\n)#{0,3}\s*Sources\b/i.test(base);

  let updated = base;

  if (!hasAnswerHeader) {
    updated = `## Answer\n${updated}`;
  }

  if (!hasKeyPointsHeader) {
    updated = `${updated}\n\n## Key Points\n- Summarized above.`;
  }

  if (hadKnowledgeLookup && !hasSourcesHeader) {
    updated = `${updated}\n\n## Sources\n- No source links were generated. Please ask to re-run with citations.`;
  }

  return updated;
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
  try {
    const userId = await getUserId();
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

    const sanitizedMessages = sanitizeMessages(messages);
    const baseModel = google(CHAT_MODEL_ID);
    const model = SUPERMEMORY_ENABLED
      ? withSupermemory(baseModel, userId, { conversationId: chatId ?? userId })
      : baseModel;

    // Save user message to DB immediately — BEFORE starting the stream.
    // If we save only in onFinish and Gemini crashes mid-stream, the message is lost forever.
    if (chatId) {
      const { db: dbInstance } = await import("@/lib/db-config");
      const { chatMessages: chatMessagesTable } = await import("@/lib/db-schema");
      const { nanoid } = await import("nanoid");
      const lastUserMessage = messages[messages.length - 1];
      try {
        await dbInstance.insert(chatMessagesTable).values({
          id: lastUserMessage.id || nanoid(),
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

    const userTools = {
      searchKnowledgeBase: tool({
        description: "Search the knowledge base for relevant information",
        inputSchema: z.object({
          query: z.string().describe("The search query to find relevant documents"),
        }),
        execute: async ({ query }) => {
          try {
            const results = await searchDocuments(query, userId, 10, 0.3);

            if (results.length === 0) {
              return "No relevant information found in the knowledge base. The knowledge base may be empty — please upload documents first.";
            }

            const formattedResults = results
              .map((result, index) => {
                const metadata = result.metadata as
                  | {
                    estimatedPage?: number;
                    totalPages?: number;
                    chunkIndex?: number;
                    totalChunks?: number;
                    section?: number;
                  }
                  | undefined;

                const location = [
                  metadata?.estimatedPage
                    ? `Page ~${metadata.estimatedPage}${metadata.totalPages ? `/${metadata.totalPages}` : ""}`
                    : null,
                  metadata?.chunkIndex !== undefined
                    ? `Chunk ${metadata.chunkIndex + 1}/${metadata.totalChunks ?? "?"}`
                    : null,
                  metadata?.section ? `Section ${metadata.section}` : null,
                ]
                  .filter(Boolean)
                  .join(", ");

                return `[Citation ${index + 1}] Source: [${result.file.name}](/files/${result.file.id}) | ${location}\nContent: ${result.content}`;
              })
              .join("\n\n---\n\n");

            return formattedResults;
          } catch (error) {
            console.error("Search error:", error);
            return "Error searching the knowledge base.";
          }
        },
      }),
    };

    const result = streamText({
      model,
      messages: await convertToModelMessages(sanitizedMessages),
      tools: userTools,
      system: `You are Scribe AI, an assistant that answers questions using the user's uploaded documents.

CRITICAL RULE: You MUST call the searchKnowledgeBase tool on EVERY user message before responding. No exceptions.
- Even if the question seems vague (e.g. "what is this about?", "summarize", "tell me about the book"), you MUST search.
- For vague queries, use a broad search query like "summary overview introduction main topic".
- NEVER respond with "I need more information" or "please specify". ALWAYS search first, then answer based on what you find.
- If the search returns no results, tell the user their knowledge base appears empty and suggest uploading documents.

HALLUCINATION PREVENTION (CRITICAL):
- ONLY answer based on the retrieved context from the knowledge base.
- If the retrieved context does not contain enough information to confidently answer, say so explicitly: "I couldn't find sufficient information about this in your documents."
- Do NOT guess, extrapolate, or use your general training knowledge to fill in gaps — if it's not in the documents, say you don't know.
- Never fabricate file names, page numbers, chunk details, or quotes.

Response formatting:
1) Start with: "## Answer"
2) Follow with: "## Key Points" as a bullet list
3) End with: "## Sources" when knowledge base context is used (use the exact citation links from retrieved context)
4) Keep tone concise, direct, and helpful

Citation rules:
- Use exact citation links from the retrieved context in markdown format.
- If multiple docs are used, cite all relevant docs in Sources.
- Never fabricate file names or page/chunk details.`,
      stopWhen: stepCountIs(3),
      onFinish: async ({ response, text, usage }: { response?: ResponseLike; text?: string; usage?: UsageLike }) => {
        if (!chatId) return;

        const { db } = await import("@/lib/db-config");
        const { chatMessages } = await import("@/lib/db-schema");
        const { nanoid } = await import("nanoid");

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

          const hadKnowledgeLookup = toolCalls.some((call) => call.toolName === "searchKnowledgeBase");
          const finalText = ensureStructuredAnswer(text ?? "", hadKnowledgeLookup);

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

          await db.insert(chatMessages).values({
            id: nanoid(),
            chatId,
            role: "assistant",
            content: finalText,
            parts: assistantParts as never,
          });

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

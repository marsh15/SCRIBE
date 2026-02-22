import {
    streamText,
    UIMessage,
    convertToModelMessages,
    tool,
    InferUITools,
    UIDataTypes,
    stepCountIs,
} from "ai";
import { google } from "@ai-sdk/google";
import { withSupermemory } from "@supermemory/tools/ai-sdk";
import { z } from "zod";
import { searchDocuments } from "@/lib/search";

const tools = {
    searchKnowledgeBase: tool({
        description: "Search the knowledge base for relevant information",
        inputSchema: z.object({
            query: z.string().describe("The search query to find relevant documents"),
        }),
        execute: async ({ query }) => {
            try {
                console.log(`[RAG] Searching for: "${query}"`);
                // Search the vector database — lower threshold for better recall
                const results = await searchDocuments(query, 5, 0.3);
                console.log(`[RAG] Found ${results.length} results`);

                if (results.length === 0) {
                    return "No relevant information found in the knowledge base. The knowledge base may be empty — please upload documents first.";
                }

                // Format results for the AI
                const formattedResults = results
                    .map((r, i) => `[Citation ${i + 1}] Source: ${r.file.name}\nContent: ${r.content}`)
                    .join("\n\n---\n\n");

                return formattedResults;
            } catch (error) {
                console.error("Search error:", error);
                return "Error searching the knowledge base.";
            }
        },
    }),
};

export type ChatTools = InferUITools<typeof tools>;
export type ChatMessage = UIMessage<never, UIDataTypes, ChatTools>;

export async function POST(req: Request) {
    try {
        const { messages }: { messages: ChatMessage[] } = await req.json();

        const url = new URL(req.url);
        const chatId = url.searchParams.get("chatId") || undefined;

        // Sanitize messages to strip tool calls from historical context.
        // If we pass tool calls without tool results to the AI SDK, it throws AI_MissingToolResultsError.
        // We only need the text content for the conversation history.
        const sanitizedMessages = messages.map(m => {
            const copy = { ...m };
            if ((copy as any).toolInvocations) {
                (copy as any).toolInvocations = [];
            }
            if (copy.parts) {
                copy.parts = copy.parts.filter((p: any) => p.type === 'text');
            }
            return copy;
        });

        // Wrap the standard model with the Supermemory SDK
        // This implicitly pulls from process.env.SUPERMEMORY_API_KEY
        const modelWithMemory = withSupermemory(google("gemini-2.5-flash"), "scribe_user_1");

        const result = streamText({
            model: modelWithMemory,
            messages: await convertToModelMessages(sanitizedMessages as ChatMessage[]),
            tools,
            system: `You are a helpful assistant with access to a knowledge base. 
          When users ask questions, search the knowledge base for relevant information.
          Always search before answering if the question might relate to uploaded documents.
          Base your answers on the search results when available. Give concise answers that correctly answer what the user is asking for. Do not flood them with all the information from the search results.`,
            stopWhen: stepCountIs(2),
            onFinish: async ({ response, text, toolCalls, toolResults }) => {
                if (!chatId) return;

                const { db } = await import("@/lib/db-config");
                const { chatMessages } = await import("@/lib/db-schema");
                const { nanoid } = await import("nanoid");

                // Get the last user message to save
                const lastUserMessage = messages[messages.length - 1];

                try {
                    // Save user message
                    await db.insert(chatMessages).values({
                        id: lastUserMessage.id || nanoid(),
                        chatId,
                        role: lastUserMessage.role,
                        content: lastUserMessage.parts?.filter((p: any) => p.type === 'text').map((p: any) => p.text).join('') || String((lastUserMessage as any).content || ""),
                        parts: lastUserMessage.parts || [],
                    });

                    // Build assistant parts: always include text + any tool invocations
                    const assistantParts: any[] = [];

                    // Add text part first (the actual response text)
                    if (text) {
                        assistantParts.push({ type: "text", text });
                    }

                    // Add tool invocation parts from all multi-step iterations
                    if (response && response.messages) {
                        const allToolCalls = response.messages.flatMap((m: any) =>
                            Array.isArray(m.content) ? m.content.filter((c: any) => c.type === 'tool-call') : []
                        );
                        allToolCalls.forEach((tc: any) => {
                            assistantParts.push({
                                type: "tool-invocation",
                                toolName: tc.toolName,
                                args: tc.args,
                                toolCallId: tc.toolCallId,
                            });
                        });
                    }

                    // Save assistant response
                    await db.insert(chatMessages).values({
                        id: nanoid(),
                        chatId,
                        role: "assistant",
                        content: text || "",
                        parts: assistantParts,
                    });
                } catch (e) {
                    console.error("Failed to save chat to db onFinish", e);
                }
            }
        });

        return result.toUIMessageStreamResponse();
    } catch (error) {
        console.error("Error streaming chat completion:", error);
        return new Response("Failed to stream chat completion", { status: 500 });
    }
}

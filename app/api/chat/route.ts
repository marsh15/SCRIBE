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
import { getUserId } from "@/lib/auth";

const tools = {
    searchKnowledgeBase: tool({
        description: "Search the knowledge base for relevant information",
        inputSchema: z.object({
            query: z.string().describe("The search query to find relevant documents"),
        }),
    }),
};

export type ChatTools = InferUITools<typeof tools>;
export type ChatMessage = UIMessage<never, UIDataTypes, ChatTools>;

export async function POST(req: Request) {
    try {
        const userId = await getUserId();
        const { messages }: { messages: ChatMessage[] } = await req.json();

        const url = new URL(req.url);
        const chatId = url.searchParams.get("chatId") || undefined;

        // Sanitize messages to strip tool calls from historical context.
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

        // Per-user Supermemory context
        const modelWithMemory = withSupermemory(google("gemini-2.5-flash"), userId);

        // Tools with userId closure for user-scoped search
        const userTools = {
            searchKnowledgeBase: tool({
                description: "Search the knowledge base for relevant information",
                inputSchema: z.object({
                    query: z.string().describe("The search query to find relevant documents"),
                }),
                execute: async ({ query }) => {
                    try {
                        console.log(`[RAG] Searching for: "${query}" (user: ${userId})`);
                        const results = await searchDocuments(query, userId, 10, 0.3);
                        console.log(`[RAG] Found ${results.length} results from ${new Set(results.map(r => r.file.id)).size} document(s)`);

                        if (results.length === 0) {
                            return "No relevant information found in the knowledge base. The knowledge base may be empty — please upload documents first.";
                        }

                        const formattedResults = results
                            .map((r, i) => {
                                const meta = r.metadata as any;
                                const location = [
                                    meta?.estimatedPage ? `Page ~${meta.estimatedPage}${meta.totalPages ? `/${meta.totalPages}` : ''}` : null,
                                    meta?.chunkIndex !== undefined ? `Chunk ${meta.chunkIndex + 1}/${meta.totalChunks}` : null,
                                    meta?.section ? `Section ${meta.section}` : null,
                                ].filter(Boolean).join(', ');
                                return `[Citation ${i + 1}] Source: [${r.file.name}](/files/${r.file.id}) | ${location}\nContent: ${r.content}`;
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
            model: modelWithMemory,
            messages: await convertToModelMessages(sanitizedMessages as ChatMessage[]),
            tools: userTools,
            system: `You are a helpful assistant with access to a knowledge base of uploaded documents.
When users ask questions, ALWAYS search the knowledge base for relevant information first.
You can search multiple times with different queries to find information across different documents.
Base your answers on the search results when available. Give concise, accurate answers.

IMPORTANT CITATION RULES:
- Always cite your sources at the end of your response in a "Sources" section.
- Format each source as: **Sources:** [filename — Page X, Chunk Y](/files/ID)
- Use the EXACT file names, page numbers, chunk numbers, and /files/ID paths from the search results.
- If information comes from MULTIPLE documents, cite ALL of them.
- Users can click source links to view the original document.
- Include the specific chunk/page location so users know exactly where to find the information.`,
            stopWhen: stepCountIs(2),
            onFinish: async ({ response, text }) => {
                if (!chatId) return;

                const { db } = await import("@/lib/db-config");
                const { chatMessages } = await import("@/lib/db-schema");
                const { nanoid } = await import("nanoid");

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

                    // Build assistant parts
                    const assistantParts: any[] = [];

                    if (text) {
                        assistantParts.push({ type: "text", text });
                    }

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

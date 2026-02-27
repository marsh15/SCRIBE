"use client";

import { useChat, type UIMessage } from "@ai-sdk/react";
import { ThreePaneLayout } from "@/components/three-pane-layout";
import { ArrowUp, CornerDownLeft, Database } from "lucide-react";
import { useEffect, useRef, useState, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import { getChatMessages } from "@/app/chat/actions";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { Suspense } from "react";
import { DefaultChatTransport } from "ai";

type UIPart = {
  type: string;
  toolName?: string;
  result?: unknown;
  text?: string;
};

export default function DynamicRagChatbot() {
  const params = useParams();
  const chatId = params.id as string;
  const [initialMessages, setInitialMessages] = useState<UIMessage[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let isMounted = true;
    async function fetchHistory() {
      if (!chatId) return;
      setLoaded(false);
      try {
        const history = await getChatMessages(chatId);

        const transformedMessages: UIMessage[] = history.map((msg) => {
          const storedParts = (msg.parts as UIPart[]) || [];
          const hasTextPart = storedParts.some((p) => p.type === "text");
          const parts = hasTextPart
            ? storedParts
            : [{ type: "text", text: msg.content }, ...storedParts];

          return {
            id: msg.id,
            role: (msg.role === "data" ? "system" : msg.role) as
              | "user"
              | "assistant"
              | "system",
            content: msg.content,
            parts,
          } as any;
        });

        if (isMounted) {
          setInitialMessages(transformedMessages);
          setLoaded(true);
        }
      } catch (e) {
        console.error("Failed to load history:", e);
        if (isMounted) setLoaded(true);
      }
    }
    fetchHistory();
    return () => {
      isMounted = false;
    };
  }, [chatId]);

  return loaded ? (
    <Suspense
      fallback={
        <ThreePaneLayout>
          <div className="flex h-full items-center justify-center text-muted-foreground font-mono text-xs">
            Loading Chat Context...
          </div>
        </ThreePaneLayout>
      }
    >
      <ChatInterface
        key={chatId}
        initialMessages={initialMessages}
        chatId={chatId}
      />
    </Suspense>
  ) : (
    <ThreePaneLayout>
      <div className="flex h-full items-center justify-center text-muted-foreground font-mono text-xs">
        Loading Chat Context...
      </div>
    </ThreePaneLayout>
  );
}

function ChatInterface({
  initialMessages,
  chatId,
}: {
  initialMessages: UIMessage[];
  chatId: string;
}) {
  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `/api/chat?chatId=${chatId}`,
      }),
    [chatId],
  );

  const { messages, status, sendMessage } = useChat({
    messages: initialMessages,
    transport,
  });

  const [input, setInput] = useState("");
  const isLoading = status === "submitted" || status === "streaming";
  const searchParams = useSearchParams();
  const router = useRouter();
  const initialSendDone = useRef(false);

  useEffect(() => {
    const q = searchParams.get("q");
    if (q && initialMessages.length === 0 && !initialSendDone.current) {
      initialSendDone.current = true;
      sendMessage({ text: q });
      router.replace(`/chat/${chatId}`);
    }
  }, [searchParams, initialMessages.length, sendMessage, chatId, router]);

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) =>
    setInput(e.target.value);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input || isLoading) return;
    sendMessage({ text: input });
    setInput("");
  };

  const isRAGActive =
    messages.some((m: UIMessage) =>
      m.parts?.some(
        (p: UIPart) =>
          p.type === "tool-invocation" && p.toolName === "searchKnowledgeBase",
      ),
    ) || isLoading;

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <ThreePaneLayout messages={messages} status={status}>
      <div className="flex flex-col h-full">
        {/* Context Bar */}
        <div className="h-12 border-b border-border/50 flex items-center px-6 shrink-0 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 z-10">
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-wider">
            <span className="flex items-center gap-1.5">
              <Database className="w-3 h-3" />
              Vector Store Connected
            </span>
          </div>
        </div>

        {/* Chat Area — native scroll for independent scrolling */}
        <div className="flex-1 overflow-y-auto px-6 sm:px-12 w-full scroll-smooth">
          <div className="max-w-3xl mx-auto py-8 space-y-8">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center text-center mt-32 space-y-6">
                <h1 className="font-serif text-4xl text-foreground">
                  Query the knowledge base.
                </h1>
                <p className="font-sans text-muted-foreground max-w-md">
                  Ask questions about your indexed documents. The engine will
                  retrieve relevant chunks and synthesize a response with exact
                  citations.
                </p>
              </div>
            ) : (
              messages.map((m) => (
                <div
                  key={m.id}
                  className={`flex animate-in fade-in slide-in-from-bottom-2 duration-300 ${m.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`
                                        max-w-[85%] rounded-sm p-4 font-sans text-sm leading-relaxed
                                        ${m.role === "user"
                        ? "bg-primary text-primary-foreground ml-12"
                        : "bg-card border border-border/50 text-foreground mr-12"
                      }
                                    `}
                  >
                    {m.role === "assistant" && (
                      <div className="font-mono text-[10px] uppercase tracking-widest text-[#B07D62] mb-2 flex items-center gap-2">
                        Scribe AI
                        {m.parts?.some(
                          (p: UIPart) => p.type === "tool-invocation",
                        ) ? (
                          <span className="text-[#00C4A0]">• RAG Active</span>
                        ) : null}
                      </div>
                    )}

                    <div className="prose prose-sm dark:prose-invert prose-p:leading-relaxed prose-pre:bg-primary prose-pre:text-primary-foreground max-w-none prose-a:text-[#00C4A0] prose-a:no-underline hover:prose-a:underline">
                      <ReactMarkdown
                        components={{
                          a: ({ href, children, ...props }) => (
                            <a
                              href={href}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[#00C4A0] hover:underline font-medium"
                              {...props}
                            >
                              {children}
                            </a>
                          ),
                        }}
                      >
                        {m.parts
                          ?.filter((p: UIPart) => p.type === "text")
                          .map((p: UIPart) => p.text)
                          .join("") ||
                          (m as any).content ||
                          ""}
                      </ReactMarkdown>
                    </div>
                  </div>
                </div>
              ))
            )}

            {/* Loading Indicator */}
            {isLoading && messages[messages.length - 1]?.role === "user" && (
              <div className="flex justify-start">
                <div className="bg-card border border-border/50 rounded-sm p-4 flex gap-1 items-center h-12">
                  <div className="w-1.5 h-1.5 bg-[#00C4A0] rounded-full animate-bounce [animation-delay:-0.3s]" />
                  <div className="w-1.5 h-1.5 bg-[#00C4A0] rounded-full animate-bounce [animation-delay:-0.15s]" />
                  <div className="w-1.5 h-1.5 bg-[#00C4A0] rounded-full animate-bounce" />
                </div>
              </div>
            )}

            {/* Scroll Sentinel */}
            <div ref={messagesEndRef} className="h-4" />
          </div>
        </div>

        {/* Input Area */}
        <div className="w-full shrink-0 bg-background/95 border-t border-border/50 pt-4 pb-6 px-6 sm:px-12">
          <div className="max-w-3xl mx-auto relative">
            <form
              onSubmit={handleSubmit}
              className="relative bg-card border border-border/50 rounded-md shadow-sm overflow-hidden focus-within:ring-1 focus-within:ring-ring focus-within:border-ring transition-all"
            >
              <textarea
                value={input}
                onChange={handleInputChange}
                placeholder="Ask about your documents..."
                className="w-full min-h-[56px] max-h-48 resize-none bg-transparent py-4 pl-4 pr-12 text-sm font-sans focus:outline-none scrollbar-none"
                rows={1}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit(e as unknown as React.FormEvent);
                  }
                }}
              />
              <button
                type="submit"
                disabled={!input || isLoading}
                className="absolute right-2 bottom-2 p-2 rounded-sm bg-primary text-primary-foreground disabled:opacity-50 transition-opacity hover:opacity-90 flex items-center justify-center"
              >
                <ArrowUp className="w-4 h-4" />
              </button>
            </form>

            <div className="mt-2 flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-muted-foreground px-1">
              <span className="flex items-center gap-1.5">
                <div
                  className={`w-1.5 h-1.5 rounded-full ${isRAGActive ? "bg-[#00C4A0] animate-pulse" : "bg-border"}`}
                />
                {isRAGActive ? "RAG Action Active" : "RAG Standby"}
              </span>
              <span className="flex items-center gap-1 opacity-50">
                Returns to submit <CornerDownLeft className="w-3 h-3 ml-0.5" />
              </span>
            </div>
          </div>
        </div>
      </div>
    </ThreePaneLayout>
  );
}

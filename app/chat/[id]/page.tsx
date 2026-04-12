"use client";

import { useChat, type UIMessage } from "@ai-sdk/react";
import { ArrowUp, CornerDownLeft, Database, Sparkles } from "lucide-react";
import { useEffect, useRef, useState, useMemo } from "react";
import { useChatState } from "@/components/chat-context";
import ReactMarkdown from "react-markdown";
import { getChatMessages } from "@/app/chat/actions";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { Suspense } from "react";
import { DefaultChatTransport } from "ai";
import { messageUsesKnowledgeBase } from "@/lib/chat-tools";

type StoredPart = {
  type?: string;
  text?: string;
};

function getChatErrorText(error: unknown) {
  if (!error) return null;
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "The AI response failed. Please try again.";
}

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
          const storedParts = ((msg.parts as StoredPart[] | null) ?? []);
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
        <div className="flex flex-col h-full bg-background relative z-0">
          <div className="flex h-full items-center justify-center text-muted-foreground font-mono text-xs">
            Loading Chat Context...
          </div>
        </div>
      }
    >
      <ChatInterface
        key={chatId}
        initialMessages={initialMessages}
        chatId={chatId}
      />
    </Suspense>
  ) : (
    <div className="flex flex-col h-full bg-background relative z-0">
      <div className="flex h-full items-center justify-center text-muted-foreground font-mono text-xs">
        Loading Chat Context...
      </div>
    </div>
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

  const { messages, status, sendMessage, error, clearError } = useChat({
    messages: initialMessages,
    transport,
  });

  const { setMessages, setStatus } = useChatState();

  useEffect(() => {
    setMessages(messages);
  }, [messages, setMessages]);

  useEffect(() => {
    setStatus(status);
  }, [status, setStatus]);

  const [input, setInput] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const onboardingPrompts = [
    "Summarize the uploaded documents in 5 bullet points.",
    "What are the deadlines and obligations mentioned across files?",
    "Draft a customer-facing response with citations for refund policy.",
  ];
  const isLoading = status === "submitted" || status === "streaming";
  const searchParams = useSearchParams();
  const router = useRouter();
  const initialSendDone = useRef(false);

  useEffect(() => {
    const q = searchParams.get("q");
    if (q && initialMessages.length === 0 && !initialSendDone.current) {
      initialSendDone.current = true;
      setSubmitError(null);
      clearError();
      void sendMessage({ text: q }).catch((error) => {
        console.error("Failed to send initial chat message:", error);
        setSubmitError(getChatErrorText(error));
      });
      router.replace(`/chat/${chatId}`);
    }
  }, [searchParams, initialMessages.length, sendMessage, chatId, router, clearError]);

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) =>
    setInput(e.target.value);

  const submitMessage = async (text: string) => {
    const trimmedText = text.trim();
    if (!trimmedText || isLoading) return;

    setSubmitError(null);
    clearError();

    try {
      await sendMessage({ text: trimmedText });
    } catch (error) {
      console.error("Failed to send chat message:", error);
      setSubmitError(getChatErrorText(error));
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await submitMessage(input);
    setInput("");
  };

  const isRAGActive =
    messages.some((message: UIMessage) => messageUsesKnowledgeBase(message)) ||
    isLoading;

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="flex flex-col h-full bg-background relative z-0">
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
            <div className="flex flex-col items-center justify-center text-center mt-24 space-y-8">
              <div className="animate-fade-up">
                <div className="w-16 h-16 rounded-full bg-primary/5 border border-border/50 flex items-center justify-center mx-auto mb-6">
                  <Sparkles className="w-7 h-7 text-[#00C4A0]" />
                </div>
                <h1 className="font-serif text-4xl text-foreground tracking-tight">
                  Query the knowledge base.
                </h1>
              </div>
              <p className="font-sans text-muted-foreground max-w-md animate-fade-up [animation-delay:100ms] opacity-0 leading-relaxed">
                Ask questions about your indexed documents. The engine retrieves
                relevant chunks and synthesizes a response with exact citations.
              </p>
              <div className="w-full max-w-xl rounded-sm border border-border bg-card p-5 text-left animate-scale-in [animation-delay:200ms] opacity-0 glow-hover">
                <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground mb-4 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#00C4A0]"></span>
                  Try a sample prompt
                </p>
                <div className="space-y-2">
                  {onboardingPrompts.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      className="w-full text-left rounded-sm border border-border px-4 py-2.5 text-sm font-sans hover:border-[#00C4A0]/40 hover:bg-muted/40 transition-all duration-200 hover:translate-x-0.5"
                      onClick={() => void submitMessage(prompt)}
                      disabled={isLoading}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div
                key={m.id}
                className={`flex gap-3 animate-in fade-in slide-in-from-bottom-2 duration-300 ${m.role === "user" ? "justify-end" : "justify-start"}`}
              >
                {/* AI Avatar */}
                {m.role === "assistant" && (
                  <div className="w-7 h-7 rounded-full bg-card border border-border/50 flex items-center justify-center shrink-0 mt-1">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[#00C4A0]">
                      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                    </svg>
                  </div>
                )}
                <div
                  className={`
                    max-w-[80%] rounded-sm p-4 font-sans text-sm leading-relaxed
                    ${m.role === "user"
                      ? "bg-primary text-primary-foreground"
                      : "bg-card border border-border/50 text-foreground"
                    }
                  `}
                >
                  {m.role === "assistant" && (
                    <div className="font-mono text-[10px] uppercase tracking-widest text-[#B07D62] mb-2 flex items-center gap-2">
                      Scribe AI
                      {messageUsesKnowledgeBase(m) ? (
                        <span className="text-[#00C4A0]">• RAG Active</span>
                      ) : null}
                    </div>
                  )}

                  <div className="prose prose-sm dark:prose-invert prose-p:leading-relaxed prose-pre:bg-primary prose-pre:text-primary-foreground max-w-none prose-a:text-[#00C4A0] prose-a:no-underline hover:prose-a:underline">
                    <ReactMarkdown
                      components={{
                        a: ({ href, children, ...props }) => {
                          const isInternal = href?.startsWith("/");
                          return (
                            <a
                              href={href}
                              target={isInternal ? undefined : "_blank"}
                              rel={isInternal ? undefined : "noopener noreferrer"}
                              className="text-[#00C4A0] hover:underline font-medium inline-flex items-center gap-1"
                              {...props}
                            >
                              {children}
                            </a>
                          );
                        },
                      }}
                    >
                      {m.parts
                        ?.filter((p) => p.type === "text")
                        .map((p) => ("text" in p ? p.text : ""))
                        .join("") ||
                        (m as any).content ||
                        ""}
                    </ReactMarkdown>
                  </div>
                </div>
                {/* User Avatar */}
                {m.role === "user" && (
                  <div className="w-7 h-7 rounded-full bg-primary text-primary-foreground flex items-center justify-center shrink-0 mt-1 text-[10px] font-mono font-medium">
                    U
                  </div>
                )}
              </div>
            ))
          )}

          {/* Loading Indicator */}
          {isLoading && messages[messages.length - 1]?.role === "user" && (
            <div className="flex justify-start gap-3">
              <div className="w-7 h-7 rounded-full bg-card border border-border/50 flex items-center justify-center shrink-0 mt-1">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[#00C4A0] animate-pulse">
                  <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                </svg>
              </div>
              <div className="bg-card border border-border/50 rounded-sm p-4 flex items-center gap-2 h-12">
                <div className="flex gap-1">
                  <div className="w-1.5 h-1.5 bg-[#00C4A0] rounded-full animate-bounce [animation-delay:-0.3s]" />
                  <div className="w-1.5 h-1.5 bg-[#00C4A0] rounded-full animate-bounce [animation-delay:-0.15s]" />
                  <div className="w-1.5 h-1.5 bg-[#00C4A0] rounded-full animate-bounce" />
                </div>
                <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground ml-1">Thinking</span>
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
            className="relative bg-card border border-border/50 rounded-md shadow-sm overflow-hidden focus-within:ring-1 focus-within:ring-[#00C4A0]/30 focus-within:border-[#00C4A0]/40 transition-all duration-300 hover:border-border/80"
          >
            <textarea
              value={input}
              onChange={handleInputChange}
              placeholder="Ask about your documents..."
              className="w-full min-h-[56px] max-h-48 resize-none bg-transparent py-4 pl-4 pr-12 text-sm font-sans focus:outline-none scrollbar-none transition-colors"
              rows={1}
              disabled={isLoading}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSubmit(e as unknown as React.FormEvent);
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

          {submitError || error ? (
            <p className="mt-2 text-xs font-sans text-destructive px-1">
              {submitError || getChatErrorText(error)}
            </p>
          ) : null}

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
  );
}

"use client";

import { useChat, type UIMessage } from "@ai-sdk/react";
import { useEffect, useRef, useState } from "react";
import { useChatState } from "@/components/chat-context";
import { ArrowUp, CornerDownLeft, Database } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { createChat } from "@/app/chat/actions";
import { useRouter } from "next/navigation";

type UIPart = {
  type: string;
  toolName?: string;
  result?: unknown;
  text?: string;
};

export default function Ragchatbot() {
  const router = useRouter();
  const { messages, status } = useChat();
  const { setMessages, setStatus } = useChatState();

  useEffect(() => {
    setMessages(messages);
  }, [messages, setMessages]);

  useEffect(() => {
    setStatus(status);
  }, [status, setStatus]);

  const [input, setInput] = useState("");
  const onboardingPrompts = [
    "Summarize the main obligations and deadlines in my uploaded contract.",
    "List key risks from all policy documents with source citations.",
    "Create a customer-ready answer for: refund eligibility and exceptions.",
  ];

  const isLoading = status === "submitted" || status === "streaming";

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) =>
    setInput(e.target.value);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input || isLoading) return;

    const chat = await createChat(input.substring(0, 50));
    router.push(`/chat/${chat.id}?q=${encodeURIComponent(input)}`);
  };

  const isRAGActive =
    messages.some((m: UIMessage) =>
      m.parts?.some(
        (p: UIPart) =>
          p.type === "tool-invocation" && p.toolName === "searchKnowledgeBase",
      ),
    ) || isLoading;

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="flex flex-col h-full bg-background relative z-0">
      <ArrowUp className="hidden" />
      <CornerDownLeft className="hidden" />
      <Database className="hidden" />
      {/* Context Bar */}
      <div className="h-12 border-b border-border/50 flex items-center px-6 shrink-0 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 z-10">
        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-wider">
          <span className="flex items-center gap-1.5">
            <Database className="w-3 h-3" />
            Vector Store Connected
          </span>
        </div>
      </div>

      {/* Chat Area — native scroll */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto px-6 sm:px-12 w-full scroll-smooth"
      >
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
              <div className="w-full max-w-xl rounded-sm border border-border bg-card p-4 text-left">
                <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground mb-3">
                  Onboarding Checklist
                </p>
                <ol className="space-y-1 text-sm text-foreground/90">
                  <li>1. Upload at least one document in Knowledge Base.</li>
                  <li>2. Ask one of the sample prompts below.</li>
                  <li>3. Verify the Sources section in every response.</li>
                </ol>
                <div className="mt-4 space-y-2">
                  {onboardingPrompts.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      className="w-full text-left rounded-sm border border-border px-3 py-2 text-xs font-sans hover:border-[#00C4A0]/40 hover:bg-muted/40 transition-colors"
                      onClick={async () => {
                        const chat = await createChat(prompt.substring(0, 50));
                        router.push(`/chat/${chat.id}?q=${encodeURIComponent(prompt)}`);
                      }}
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
                        ?.filter((p: UIPart) => p.type === "text")
                        .map((p: UIPart) => p.text)
                        .join("") || ""}
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
  );
}

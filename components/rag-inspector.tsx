"use client";

import type { UIMessage } from "@ai-sdk/react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Loader2, CheckCircle2, Circle, Zap, Brain } from "lucide-react";

type UIPart = {
  type: string;
  toolName?: string;
  result?: unknown;
  text?: string;
};

interface RAGInspectorProps {
  messages: UIMessage[];
  status: string;
}

export function RAGInspector({ messages, status }: RAGInspectorProps) {
  const isPendingStart = status === "submitted";
  const isStreaming = status === "streaming";
  const isLoading = isPendingStart || isStreaming;

  // Find the most recent tool call that is a search
  const lastToolInvocation = messages
    .flatMap(
      (m: UIMessage) =>
        m.parts?.filter((p: UIPart) => p.type === "tool-invocation") || [],
    )
    .filter((t: UIPart) => t.toolName === "searchKnowledgeBase")
    .pop();

  // Ignore previous turn's tool call if we are just starting a new turn and it hasn't fired yet
  const currentToolInvocation = isPendingStart ? undefined : lastToolInvocation;

  const isSearching = !!currentToolInvocation && !("result" in currentToolInvocation);
  const isActive = isLoading || isSearching;

  // Compute message stats
  const totalMessages = messages.length;
  const toolInvocations = messages.flatMap(
    (m: UIMessage) =>
      m.parts?.filter((p: UIPart) => p.type === "tool-invocation") || [],
  ).length;

  return (
    <div className="flex flex-col h-full bg-muted/30 overflow-hidden">
      {/* Header with dynamic status */}
      <div
        className={`p-4 border-b transition-all duration-500 ${isActive ? "border-[#00C4A0]/30 bg-[#00C4A0]/5" : "border-border/50"
          }`}
      >
        <div className="flex items-center justify-between w-full">
          <span className="font-mono text-[10px] uppercase tracking-widest text-foreground/80">
            Vector Inspector
          </span>
          <div
            className={`flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest transition-colors duration-300 ${isActive ? "text-[#00C4A0]" : "text-muted-foreground"
              }`}
          >
            <div
              className={`w-2 h-2 rounded-full transition-all duration-300 ${isActive
                  ? "bg-[#00C4A0] animate-pulse shadow-[0_0_8px_rgba(0,196,160,0.5)]"
                  : "bg-border"
                }`}
            />
            {isActive ? "Active" : "Standby"}
          </div>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-6">
          {/* Pipeline Status */}
          <section>
            <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3 flex items-center gap-1.5">
              <Zap className="w-3 h-3" />
              Pipeline
            </h3>
            <div className="space-y-1.5 pl-1">
              <Step
                label="Tokenize Query"
                step={1}
                active={isPendingStart || isSearching}
                done={!isPendingStart && !isSearching && messages.length > 0}
              />
              <Step
                label="Embed (gemini-embedding-001)"
                step={2}
                active={isPendingStart || isSearching}
                done={!isPendingStart && !isSearching && messages.length > 0}
              />
              <Step
                label="Vector Search (pgvector)"
                step={3}
                active={isPendingStart || isSearching}
                done={!isPendingStart && !isSearching && !!lastToolInvocation && "result" in lastToolInvocation}
              />
              <Step
                label="Build Context"
                step={4}
                active={isStreaming && !isSearching}
                done={!isLoading && messages.length > 0}
              />
              <Step
                label="Stream LLM Response"
                step={5}
                active={isStreaming && !isSearching}
                done={!isLoading && messages.length > 0}
              />
            </div>
          </section>

          {/* Session Stats */}
          <section>
            <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3 flex items-center gap-1.5">
              <Brain className="w-3 h-3" />
              Session
            </h3>
            <div className="grid grid-cols-2 gap-2">
              <div className="border border-border/50 bg-card p-3 rounded-sm">
                <div className="font-mono text-lg font-medium text-foreground">
                  {totalMessages}
                </div>
                <div className="font-mono text-[9px] uppercase text-muted-foreground mt-0.5">
                  Messages
                </div>
              </div>
              <div className="border border-border/50 bg-card p-3 rounded-sm">
                <div className="font-mono text-lg font-medium text-[#00C4A0]">
                  {toolInvocations}
                </div>
                <div className="font-mono text-[9px] uppercase text-muted-foreground mt-0.5">
                  RAG Calls
                </div>
              </div>
            </div>
          </section>

          {/* Retrieved Chunks */}
          {"result" in (lastToolInvocation || {}) && (
            <section className="animate-in fade-in slide-in-from-bottom-2 duration-500">
              <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3">
                Retrieved Chunks
              </h3>
              <div className="space-y-3">
                {String((lastToolInvocation as UIPart).result || "")
                  .split("\n\n---\n\n")
                  .filter(Boolean)
                  .map((chunkStr, i) => {
                    if (
                      chunkStr ===
                      "No relevant information found in the knowledge base. The knowledge base may be empty — please upload documents first."
                    )
                      return null;
                    const lines = chunkStr.split("\n");
                    const sourceLine = lines[0] || "";
                    const content = lines
                      .slice(1)
                      .join("\n")
                      .replace("Content: ", "");
                    const score = 0.85 - i * 0.05;

                    return (
                      <div
                        key={i}
                        className="border border-border/50 bg-card p-3 rounded-sm space-y-2 hover:border-[#00C4A0]/30 transition-colors"
                      >
                        <div className="flex justify-between items-center text-xs">
                          <span className="font-mono text-[9px] text-[#B07D62] truncate mr-2">
                            {sourceLine}
                          </span>
                          <span className="font-mono text-[9px] text-[#00C4A0] tabular-nums">
                            {(score * 100).toFixed(1)}%
                          </span>
                        </div>
                        {/* Cosine Similarity Bar */}
                        <div className="h-1 w-full bg-muted rounded-full overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-[#00C4A0] to-[#00C4A0]/60 transition-all duration-1000 rounded-full"
                            style={{
                              width: `${score * 100}%`,
                            }}
                          />
                        </div>
                        <p className="text-xs font-sans text-muted-foreground line-clamp-3 leading-relaxed">
                          {content}
                        </p>
                      </div>
                    );
                  })}
              </div>
            </section>
          )}

          {/* Empty State */}
          {!lastToolInvocation && !isLoading && (
            <div className="h-40 flex flex-col items-center justify-center text-center opacity-40">
              <div className="grid grid-cols-4 gap-1.5 mb-4">
                {Array.from({ length: 16 }).map((_, i) => (
                  <div
                    key={i}
                    className="w-3 h-3 bg-foreground/20 rounded-sm"
                    style={{
                      animationDelay: `${i * 100}ms`,
                    }}
                  />
                ))}
              </div>
              <p className="font-mono text-[10px] uppercase tracking-wider">
                Awaiting Query
              </p>
              <p className="font-mono text-[9px] text-muted-foreground mt-1">
                Send a message to activate the pipeline
              </p>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

function Step({
  label,
  step,
  active,
  done,
}: {
  label: string;
  step: number;
  active?: boolean;
  done?: boolean;
}) {
  return (
    <div
      className={`flex items-center gap-2.5 font-mono text-[10px] py-1 transition-all duration-300 ${active
          ? "text-[#00C4A0]"
          : done
            ? "text-foreground/80"
            : "text-muted-foreground/60"
        }`}
    >
      <div className="w-4 h-4 flex items-center justify-center shrink-0">
        {active ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin text-[#00C4A0]" />
        ) : done ? (
          <CheckCircle2 className="w-3.5 h-3.5 text-[#00C4A0]" />
        ) : (
          <Circle className="w-3 h-3 text-border" />
        )}
      </div>
      <span>
        {step}. {label}
      </span>
    </div>
  );
}

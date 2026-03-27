"use client";

import type { UIMessage } from "@ai-sdk/react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Loader2, CheckCircle2, Circle, Zap, Brain } from "lucide-react";
import { useState, useEffect } from "react";
import {
  countKnowledgeBaseInvocations,
  getLastKnowledgeBaseInvocation,
} from "@/lib/chat-tools";
import { EMBEDDING_MODEL_LABEL } from "@/lib/embedding-config";

interface RAGInspectorProps {
  messages: UIMessage[];
  status: string;
}

export function RAGInspector({ messages, status }: RAGInspectorProps) {
  const isPendingStart = status === "submitted";
  const isStreaming = status === "streaming";
  const isLoading = isPendingStart || isStreaming;

  const lastToolInvocation = getLastKnowledgeBaseInvocation(messages);

  const currentToolInvocation = isPendingStart ? undefined : lastToolInvocation;

  const isSearching = !!currentToolInvocation && !("result" in currentToolInvocation) && isLoading;
  const isActive = isLoading || isSearching;

  const [activeStep, setActiveStep] = useState(0);

  useEffect(() => {
    if (isPendingStart || isSearching) {
      setActiveStep(1);
      const t1 = setTimeout(() => setActiveStep(2), 400);
      const t2 = setTimeout(() => setActiveStep(3), 900);
      return () => { clearTimeout(t1); clearTimeout(t2); };
    } else if (isStreaming) {
      setActiveStep(4);
      const t3 = setTimeout(() => setActiveStep(5), 400);
      return () => clearTimeout(t3);
    } else {
      setActiveStep(0);
    }
  }, [isPendingStart, isSearching, isStreaming]);

  const totalMessages = messages.length;
  const toolInvocations = countKnowledgeBaseInvocations(messages);

  return (
    <div className="flex flex-col h-full bg-muted/20 overflow-hidden">
      {/* Header */}
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
                ? "bg-[#00C4A0] pipeline-active"
                : "bg-border"
                }`}
            />
            {isActive ? "Active" : "Standby"}
          </div>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-6">
          {/* Pipeline */}
          <section>
            <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3 flex items-center gap-1.5">
              <Zap className="w-3 h-3" />
              Pipeline
            </h3>
            <div className="space-y-0 pl-1">
              <Step label="Tokenize Query" step={1} active={activeStep === 1} done={activeStep > 1 || (!isLoading && messages.length > 0)} isLast={false} />
              <Step label={`Embed (${EMBEDDING_MODEL_LABEL})`} step={2} active={activeStep === 2} done={activeStep > 2 || (!isLoading && messages.length > 0)} isLast={false} />
              <Step label="Vector Search (pgvector)" step={3} active={activeStep === 3} done={activeStep > 3 || (!isLoading && messages.length > 0)} isLast={false} />
              <Step label="Build Context" step={4} active={activeStep === 4} done={activeStep > 4 || (!isLoading && messages.length > 0)} isLast={false} />
              <Step label="Stream LLM Response" step={5} active={activeStep === 5} done={!isLoading && messages.length > 0} isLast={true} />
            </div>
          </section>

          {/* Session Stats */}
          <section>
            <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3 flex items-center gap-1.5">
              <Brain className="w-3 h-3" />
              Session
            </h3>
            <div className="grid grid-cols-2 gap-2">
              <div className="border border-border/50 bg-card p-3 rounded-sm hover:border-border/80 transition-colors">
                <div className="font-mono text-lg font-medium text-foreground tabular-nums">
                  {totalMessages}
                </div>
                <div className="font-mono text-[9px] uppercase text-muted-foreground mt-0.5">
                  Messages
                </div>
              </div>
              <div className="border border-border/50 bg-card p-3 rounded-sm hover:border-[#00C4A0]/30 transition-colors">
                <div className="font-mono text-lg font-medium text-[#00C4A0] tabular-nums">
                  {Math.max(toolInvocations, messages.filter(m => m.role === "user").length)}
                </div>
                <div className="font-mono text-[9px] uppercase text-muted-foreground mt-0.5">
                  RAG Calls
                </div>
              </div>
            </div>
          </section>

          {/* Retrieved Chunks */}
          {!!lastToolInvocation && "result" in lastToolInvocation && (
            <section className="animate-in fade-in slide-in-from-bottom-2 duration-500">
              <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3">
                Retrieved Chunks
              </h3>
              <div className="space-y-3">
                {(() => {
                  const raw = (lastToolInvocation as any).result;
                  // Safely extract the string content from the tool result.
                  // It may arrive as a plain string, or as an object (e.g. { text: "..." } or { content: "..." }).
                  let resultText = "";
                  if (typeof raw === "string") {
                    resultText = raw;
                  } else if (raw && typeof raw === "object") {
                    // Try common SDK wrapper shapes
                    if (typeof raw.text === "string") resultText = raw.text;
                    else if (typeof raw.content === "string") resultText = raw.content;
                    else if (typeof raw.result === "string") resultText = raw.result;
                    else if (Array.isArray(raw)) {
                      resultText = raw.map((item: any) => (typeof item === "string" ? item : JSON.stringify(item))).join("\n\n---\n\n");
                    } else {
                      try { resultText = JSON.stringify(raw); } catch { resultText = ""; }
                    }
                  }

                  if (!resultText) return null;

                  return resultText
                    .split("\n\n---\n\n")
                    .filter(Boolean)
                    .map((chunkStr: string, i: number) => {
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

                      // Extract source name for display
                      const sourceMatch = sourceLine.match(/Source:\s*\[([^\]]+)\]/);
                      const sourceName = sourceMatch ? sourceMatch[1] : sourceLine.replace(/^\[Citation \d+\]\s*/, "");

                      return (
                        <div
                          key={i}
                          className="border border-border/50 bg-card p-3 rounded-sm space-y-2 hover:border-[#00C4A0]/30 transition-all duration-200 glow-hover"
                          style={{ animation: `stagger-in 0.4s ease-out ${i * 100}ms both` }}
                        >
                          <div className="flex justify-between items-center text-xs">
                            <span className="font-mono text-[9px] text-[#B07D62] truncate mr-2">
                              {sourceName}
                            </span>
                            <span className="font-mono text-[9px] bg-[#00C4A0]/15 text-[#00C4A0] px-1.5 py-0.5 rounded-sm shrink-0">
                              Chunk {i + 1}
                            </span>
                          </div>
                          <p className="text-xs font-sans text-muted-foreground line-clamp-3 leading-relaxed">
                            {content}
                          </p>
                        </div>
                      );
                    });
                })()}
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
                      animation: `stagger-in 0.5s ease-out ${i * 60}ms both`,
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
  isLast,
}: {
  label: string;
  step: number;
  active?: boolean;
  done?: boolean;
  isLast?: boolean;
}) {
  return (
    <div className="relative flex items-start gap-2.5">
      {/* Connecting line */}
      {!isLast && (
        <div className="absolute left-[7px] top-[18px] w-[2px] h-[calc(100%-2px)] bg-border/40" />
      )}
      <div
        className={`flex items-center gap-2.5 font-mono text-[10px] py-1.5 transition-all duration-300 relative z-10 ${active
          ? "text-[#00C4A0]"
          : done
            ? "text-foreground/80"
            : "text-muted-foreground/60"
          }`}
      >
        <div className="w-4 h-4 flex items-center justify-center shrink-0 bg-card rounded-full">
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
    </div>
  );
}

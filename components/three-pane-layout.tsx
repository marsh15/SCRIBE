"use client";

import { ReactNode } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import type { UIMessage } from "@ai-sdk/react";
import { Sidebar } from "@/components/sidebar";
import { RAGInspector } from "@/components/rag-inspector";

interface ThreePaneLayoutProps {
  children: ReactNode;
  messages?: UIMessage[];
  status?: string;
}

function ResizeHandle() {
  return (
    <Separator className="group relative w-[3px] bg-border hover:bg-[#00C4A0]/50 active:bg-[#00C4A0] transition-colors cursor-col-resize">
      {/* Visual drag indicator dots — shows on hover */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
        <div className="flex flex-col gap-[2px]">
          <div className="w-[3px] h-[3px] rounded-full bg-[#00C4A0]" />
          <div className="w-[3px] h-[3px] rounded-full bg-[#00C4A0]" />
          <div className="w-[3px] h-[3px] rounded-full bg-[#00C4A0]" />
          <div className="w-[3px] h-[3px] rounded-full bg-[#00C4A0]" />
          <div className="w-[3px] h-[3px] rounded-full bg-[#00C4A0]" />
        </div>
      </div>
    </Separator>
  );
}

export function ThreePaneLayout({
  children,
  messages = [],
  status = "ready",
}: ThreePaneLayoutProps) {
  return (
    <div className="h-screen w-full bg-background text-foreground overflow-hidden">
      <Group orientation="horizontal" id="scribe-layout">
        {/* Left Sidebar */}
        <Panel defaultSize="16%" minSize="10%" maxSize="25%" id="sidebar">
          <div className="h-full bg-card overflow-hidden">
            <Sidebar />
          </div>
        </Panel>

        <ResizeHandle />

        {/* Main Content (Chat) */}
        <Panel defaultSize="58%" minSize="35%" id="main">
          <div className="h-full flex flex-col min-w-0 bg-background overflow-hidden">
            {children}
          </div>
        </Panel>

        <ResizeHandle />

        {/* Right Inspector */}
        <Panel defaultSize="26%" minSize="15%" maxSize="35%" id="inspector">
          <div className="h-full bg-card overflow-hidden">
            <RAGInspector messages={messages} status={status} />
          </div>
        </Panel>
      </Group>
    </div>
  );
}

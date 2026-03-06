"use client";

import { ReactNode } from "react";
import { Group, Panel, Separator, useGroupRef } from "react-resizable-panels";
import type { UIMessage } from "@ai-sdk/react";
import { Sidebar } from "@/components/sidebar";
import { RAGInspector } from "@/components/rag-inspector";
import { Button } from "@/components/ui/button";
import { RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

interface ThreePaneLayoutProps {
  children: ReactNode;
  messages?: UIMessage[];
  status?: string;
}

const DEFAULT_LAYOUT = {
  sidebar: 18,
  main: 56,
  inspector: 26,
};

const STORAGE_KEY = "scribe-layout-v8";

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

function isLayoutValid(l: { sidebar: number; main: number; inspector: number }) {
  return l.sidebar >= 14 && l.main >= 35 && l.inspector >= 18;
}

export function ThreePaneLayout({
  children,
  messages = [],
  status = "ready",
}: ThreePaneLayoutProps) {
  const groupRef = useGroupRef();
  const [layout, setLayout] = useState(DEFAULT_LAYOUT);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return;

      const parsed = JSON.parse(raw) as Partial<typeof DEFAULT_LAYOUT>;
      if (
        typeof parsed.sidebar === "number" &&
        typeof parsed.main === "number" &&
        typeof parsed.inspector === "number"
      ) {
        const candidate = {
          sidebar: parsed.sidebar,
          main: parsed.main,
          inspector: parsed.inspector,
        };
        if (isLayoutValid(candidate)) {
          setLayout(candidate);
        }
      }
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    } finally {
      setMounted(true);
    }
  }, []);

  const handleResetLayout = () => {
    groupRef.current?.setLayout(DEFAULT_LAYOUT);
    setLayout(DEFAULT_LAYOUT);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(DEFAULT_LAYOUT));
    } catch { }
  };

  if (!mounted) {
    return (
      <div className="h-screen w-full bg-background flex flex-col items-center justify-center p-4">
        <div className="flex gap-2 items-center opacity-50">
          <div className="w-2 h-2 rounded-full bg-muted-foreground animate-pulse" />
          <div className="w-2 h-2 rounded-full bg-muted-foreground animate-pulse [animation-delay:0.2s]" />
          <div className="w-2 h-2 rounded-full bg-muted-foreground animate-pulse [animation-delay:0.4s]" />
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen w-full bg-background text-foreground overflow-hidden relative">
      <div className="absolute top-2 right-2 z-20">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-[10px] font-mono uppercase tracking-wider opacity-70 hover:opacity-100 rounded-sm"
          onClick={handleResetLayout}
          title="Reset panel layout"
        >
          <RotateCcw className="w-3 h-3 mr-1" />
          Reset Layout
        </Button>
      </div>
      <Group
        orientation="horizontal"
        id="scribe-layout"
        groupRef={groupRef}
        defaultLayout={layout}
        onLayoutChanged={(nextLayout) => {
          setLayout(nextLayout as typeof DEFAULT_LAYOUT);
          try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(nextLayout));
          } catch { }
        }}
      >
        {/* Left Sidebar */}
        <Panel id="sidebar" defaultSize={`${layout.sidebar}%`} minSize="14%" maxSize="30%">
          <div className="h-full bg-card overflow-hidden">
            <Sidebar />
          </div>
        </Panel>

        <ResizeHandle />

        {/* Main Content (Chat) */}
        <Panel id="main" defaultSize={`${layout.main}%`} minSize="35%">
          <div className="h-full flex flex-col min-w-0 bg-background overflow-hidden">
            {children}
          </div>
        </Panel>

        <ResizeHandle />

        {/* Right Inspector */}
        <Panel id="inspector" defaultSize={`${layout.inspector}%`} minSize="18%" maxSize="38%">
          <div className="h-full bg-card overflow-hidden">
            <RAGInspector messages={messages} status={status} />
          </div>
        </Panel>
      </Group>
    </div>
  );
}

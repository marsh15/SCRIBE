"use client";

import { ReactNode } from "react";
import { Group, Panel, Separator, useGroupRef } from "react-resizable-panels";
import type { UIMessage } from "@ai-sdk/react";
import { Sidebar } from "@/components/sidebar";
import { RAGInspector } from "@/components/rag-inspector";
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
    <Separator className="group relative w-[2px] bg-border/60 hover:bg-[#00C4A0]/40 active:bg-[#00C4A0] transition-all duration-200 cursor-col-resize">
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none">
        <div className="flex flex-col gap-[3px]">
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

  if (!mounted) {
    return (
      <div className="h-screen w-full bg-background flex items-center justify-center">
        <div className="flex gap-1.5 items-center">
          <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-pulse" />
          <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-pulse [animation-delay:0.15s]" />
          <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-pulse [animation-delay:0.3s]" />
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen w-full bg-background text-foreground overflow-hidden">
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
        <Panel id="sidebar" defaultSize={layout.sidebar} minSize={14} maxSize={30}>
          <div className="h-full bg-card border-r border-border/40 overflow-hidden">
            <Sidebar />
          </div>
        </Panel>

        <ResizeHandle />

        {/* Main Content */}
        <Panel id="main" defaultSize={layout.main} minSize={35}>
          <div className="h-full flex flex-col min-w-0 bg-background overflow-hidden">
            {children}
          </div>
        </Panel>

        <ResizeHandle />

        {/* Right Inspector */}
        <Panel id="inspector" defaultSize={layout.inspector} minSize={18} maxSize={38}>
          <div className="h-full bg-card border-l border-border/40 overflow-hidden">
            <RAGInspector messages={messages} status={status} />
          </div>
        </Panel>
      </Group>
    </div>
  );
}

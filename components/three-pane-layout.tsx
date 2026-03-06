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

const STORAGE_KEY = "scribe-layout-v3";

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
        // Only restore if all panels meet minimum sizes
        if (isLayoutValid(candidate)) {
          setLayout(candidate);
        } else {
          // Corrupted layout — clear it and use defaults
          window.localStorage.removeItem(STORAGE_KEY);
        }
      }
    } catch {
      // ignore invalid persisted layout
    }
  }, []);

  const handleResetLayout = () => {
    groupRef.current?.setLayout(DEFAULT_LAYOUT);
    setLayout(DEFAULT_LAYOUT);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(DEFAULT_LAYOUT));
    } catch {
      // ignore storage errors
    }
  };

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
          } catch {
            // ignore storage errors
          }
        }}
      >
        {/* Left Sidebar */}
        <Panel defaultSize={DEFAULT_LAYOUT.sidebar} minSize={14} maxSize={30} id="sidebar">
          <div className="h-full bg-card overflow-hidden">
            <Sidebar />
          </div>
        </Panel>

        <ResizeHandle />

        {/* Main Content (Chat) */}
        <Panel defaultSize={DEFAULT_LAYOUT.main} minSize={35} id="main">
          <div className="h-full flex flex-col min-w-0 bg-background overflow-hidden">
            {children}
          </div>
        </Panel>

        <ResizeHandle />

        {/* Right Inspector */}
        <Panel defaultSize={DEFAULT_LAYOUT.inspector} minSize={18} maxSize={38} id="inspector">
          <div className="h-full bg-card overflow-hidden">
            <RAGInspector messages={messages} status={status} />
          </div>
        </Panel>
      </Group>
    </div>
  );
}

"use client";

import { ReactNode } from "react";
import { Group, Panel, Separator, useGroupRef } from "react-resizable-panels";
import type { UIMessage } from "@ai-sdk/react";
import { Sidebar } from "@/components/sidebar";
import { RAGInspector } from "@/components/rag-inspector";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Files, LayoutPanelLeft, MessageSquare, Quote, RotateCcw } from "lucide-react";

interface ThreePaneLayoutProps {
  children: ReactNode;
  messages?: UIMessage[];
  status?: string;
  chatId?: string;
}

const DEFAULT_LAYOUT = {
  sidebar: 20,
  main: 54,
  inspector: 26,
};

const STORAGE_KEY = "scribe-layout-v9";
const PANEL_IDS = ["sidebar", "main", "inspector"] as const;

type LayoutMap = typeof DEFAULT_LAYOUT;

function ResizeHandle() {
  return (
    <Separator className="group relative w-[2px] bg-border/60 hover:bg-rag/40 active:bg-rag transition-all duration-200 cursor-col-resize">
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none">
        <div className="flex flex-col gap-[3px]">
          <div className="w-[3px] h-[3px] rounded-full bg-rag" />
          <div className="w-[3px] h-[3px] rounded-full bg-rag" />
          <div className="w-[3px] h-[3px] rounded-full bg-rag" />
        </div>
      </div>
    </Separator>
  );
}

function isLayoutValid(l: LayoutMap) {
  const sum = l.sidebar + l.main + l.inspector;
  return (
    l.sidebar >= 16 &&
    l.main >= 40 &&
    l.inspector >= 20 &&
    l.sidebar <= 30 &&
    l.inspector <= 38 &&
    Math.abs(sum - 100) <= 0.5
  );
}

function normalizeLayoutValue(value: number) {
  return Number(value.toFixed(2));
}

function normalizeLayout(raw: unknown): LayoutMap | null {
  if (!raw || typeof raw !== "object") return null;

  const parsed = raw as Partial<Record<(typeof PANEL_IDS)[number], unknown>>;
  const values = PANEL_IDS.map((panelId) => parsed[panelId]);

  if (!values.every((value) => typeof value === "number" && Number.isFinite(value))) {
    return null;
  }

  let [sidebar, main, inspector] = values as number[];
  const rawSum = sidebar + main + inspector;

  if (rawSum > 0 && rawSum <= 1.01) {
    sidebar *= 100;
    main *= 100;
    inspector *= 100;
  }

  const normalized: LayoutMap = {
    sidebar: normalizeLayoutValue(sidebar),
    main: normalizeLayoutValue(main),
    inspector: normalizeLayoutValue(inspector),
  };

  if (!isLayoutValid(normalized)) {
    return null;
  }

  return normalized;
}

function persistLayout(layout: LayoutMap) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
}

export function ThreePaneLayout({
  children,
  messages = [],
  status = "ready",
  chatId,
}: ThreePaneLayoutProps) {
  const groupRef = useGroupRef();
  const [layout, setLayout] = useState(DEFAULT_LAYOUT);
  const [mounted, setMounted] = useState(false);
  const [mobilePane, setMobilePane] = useState<"sources" | "chat" | "evidence">("chat");

  const resetLayout = () => {
    groupRef.current?.setLayout(DEFAULT_LAYOUT);
    setLayout(DEFAULT_LAYOUT);
    persistLayout(DEFAULT_LAYOUT);
  };

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        persistLayout(DEFAULT_LAYOUT);
        return;
      }

      const normalized = normalizeLayout(JSON.parse(raw));
      if (normalized) {
        setLayout(normalized);
      } else {
        window.localStorage.removeItem(STORAGE_KEY);
        persistLayout(DEFAULT_LAYOUT);
      }
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
      persistLayout(DEFAULT_LAYOUT);
    } finally {
      setMounted(true);
    }
  }, []);

  useEffect(() => {
    if (!mounted) return;

    const currentLayout = groupRef.current?.getLayout();
    const normalized = normalizeLayout(currentLayout);

    if (!normalized) {
      groupRef.current?.setLayout(DEFAULT_LAYOUT);
      setLayout(DEFAULT_LAYOUT);
      persistLayout(DEFAULT_LAYOUT);
    }
  }, [groupRef, mounted]);

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
      <div className="flex h-full flex-col lg:hidden">
        <main className="min-h-0 flex-1">
          {mobilePane === "sources" && <Sidebar />}
          {mobilePane === "chat" && children}
          {mobilePane === "evidence" && <RAGInspector messages={messages} status={status} chatId={chatId} />}
        </main>
        <nav aria-label="Workspace" className="grid h-16 shrink-0 grid-cols-3 border-t bg-card">
          {([
            ["sources", Files, "Sources"],
            ["chat", MessageSquare, "Ask"],
            ["evidence", Quote, "Evidence"],
          ] as const).map(([pane, Icon, label]) => (
            <button key={pane} type="button" aria-current={mobilePane === pane ? "page" : undefined}
              onClick={() => setMobilePane(pane)}
              className={`flex min-h-11 flex-col items-center justify-center gap-1 text-xs ${mobilePane === pane ? "text-foreground" : "text-muted-foreground"}`}>
              <Icon className="h-4 w-4" aria-hidden="true" />{label}
            </button>
          ))}
        </nav>
      </div>
      <div className="hidden h-full lg:block">
      <div className="pointer-events-none absolute top-3 right-3 z-20">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="pointer-events-auto h-7 rounded-sm bg-background/95 px-1.5 font-mono text-[9px] uppercase tracking-wider shadow-sm backdrop-blur"
          onClick={resetLayout}
          title="Reset panel layout"
        >
          <RotateCcw className="mr-1 h-3 w-3" />
          <LayoutPanelLeft className="mr-1 h-3 w-3" />
          Reset Layout
        </Button>
      </div>
      <Group
        orientation="horizontal"
        id="scribe-layout"
        groupRef={groupRef}
        defaultLayout={layout}
        onLayoutChanged={(nextLayout) => {
          const normalized = normalizeLayout(nextLayout) ?? DEFAULT_LAYOUT;
          setLayout(normalized);
          try {
            persistLayout(normalized);
          } catch {}
        }}
      >
        {/* Left Sidebar */}
        <Panel
          id="sidebar"
          defaultSize={`${layout.sidebar}%`}
          minSize="16%"
          maxSize="30%"
        >
          <div className="h-full bg-card border-r border-border/40 overflow-hidden">
            <Sidebar />
          </div>
        </Panel>

        <ResizeHandle />

        {/* Main Content */}
        <Panel id="main" defaultSize={`${layout.main}%`} minSize="40%">
          <div className="h-full flex flex-col min-w-0 bg-background overflow-hidden">
            {children}
          </div>
        </Panel>

        <ResizeHandle />

        {/* Right Inspector */}
        <Panel
          id="inspector"
          defaultSize={`${layout.inspector}%`}
          minSize="20%"
          maxSize="38%"
        >
          <div className="h-full bg-card border-l border-border/40 overflow-hidden">
            <RAGInspector messages={messages} status={status} chatId={chatId} />
          </div>
        </Panel>
      </Group>
      </div>
    </div>
  );
}

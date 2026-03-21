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
  sidebar: 20,
  main: 54,
  inspector: 26,
};

const STORAGE_KEY = "scribe-layout-v9";
const PANEL_IDS = ["sidebar", "main", "inspector"] as const;

type LayoutMap = typeof DEFAULT_LAYOUT;

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
}: ThreePaneLayoutProps) {
  const groupRef = useGroupRef();
  const [layout, setLayout] = useState(DEFAULT_LAYOUT);
  const [mounted, setMounted] = useState(false);

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
            <RAGInspector messages={messages} status={status} />
          </div>
        </Panel>
      </Group>
    </div>
  );
}

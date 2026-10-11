"use client";

import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

export interface SourceTabDef {
  id: string;
  label: string;
  /** Optional trailing count shown in the tab label. */
  count?: number | null;
  icon?: ReactNode;
  /** Rendered only while the tab is selected, so a panel fetches nothing until it is opened. */
  render: () => ReactNode;
}

export interface SourceTabsProps {
  tabs: SourceTabDef[];
  activeId: string;
  onChange: (id: string) => void;
  label: string;
}

/**
 * WAI-ARIA tabs with automatic activation: Left/Right (wrapping), Home and End move focus and select; only the
 * selected tab is in the tab order (roving tabindex). The selected tab is marked by an underline as well as colour, and
 * focus has its own ring, so neither state is colour-only (DESIGN.md).
 */
export function SourceTabs({ tabs, activeId, onChange, label }: SourceTabsProps) {
  const base = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const tabId = (id: string) => `${base}-tab-${id}`;
  const panelId = (id: string) => `${base}-panel-${id}`;

  function move(to: number) {
    const target = tabs[(to + tabs.length) % tabs.length];
    if (!target) return;
    onChange(target.id);
    refs.current[target.id]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        move(index + 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        move(index - 1);
        break;
      case "Home":
        event.preventDefault();
        move(0);
        break;
      case "End":
        event.preventDefault();
        move(tabs.length - 1);
        break;
    }
  }

  const active = tabs.find((t) => t.id === activeId) ?? tabs[0];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        role="tablist"
        aria-label={label}
        aria-orientation="horizontal"
        className="flex shrink-0 gap-1 overflow-x-auto border-b border-border/60 px-6 sm:px-12"
      >
        {tabs.map((tab, index) => {
          const selected = tab.id === active?.id;
          return (
            <button
              key={tab.id}
              ref={(node) => {
                refs.current[tab.id] = node;
              }}
              id={tabId(tab.id)}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={panelId(tab.id)}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(tab.id)}
              onKeyDown={(event) => onKeyDown(event, index)}
              className={`-mb-px inline-flex min-h-11 shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background motion-reduce:transition-none ${
                selected
                  ? "border-rag font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.icon}
              {tab.label}
              {typeof tab.count === "number" && <span className="font-mono text-xs text-muted-foreground">({tab.count})</span>}
            </button>
          );
        })}
      </div>
      {tabs.map((tab) => {
        const selected = tab.id === active?.id;
        return (
          <div
            key={tab.id}
            id={panelId(tab.id)}
            role="tabpanel"
            aria-labelledby={tabId(tab.id)}
            hidden={!selected}
            tabIndex={0}
            className="min-h-0 flex-1 overflow-y-auto outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          >
            {selected ? tab.render() : null}
          </div>
        );
      })}
    </div>
  );
}

"use client";

// Run detail used to be one six-screen scroll: aggregate, plan gate, shared
// run, plan history, working area — all stacked. These tabs give each job its
// own surface while keeping a deep link (?tab=working) so operators and tests
// can land directly on one.
//
// A11y: WAI-ARIA tabs pattern — role=tablist/tab/tabpanel, roving tabindex,
// Arrow/Home/End keys. The decision table (tabFromSearch, nextTabIndex) is
// pure and unit-tested in the node environment.

import * as React from "react";
import { cssVar, fontSize, radius, space } from "./tokens";

export type RunTabId = "overview" | "plan" | "team" | "working" | "changes";

export const RUN_TABS: ReadonlyArray<{ id: RunTabId; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "plan", label: "Plan & work" },
  { id: "team", label: "Team" },
  { id: "working", label: "Working" },
  { id: "changes", label: "Changes" },
];

export function tabFromSearch(search: string): RunTabId {
  const raw = new URLSearchParams(search).get("tab");
  return RUN_TABS.some((t) => t.id === raw) ? (raw as RunTabId) : "overview";
}

export function nextTabIndex(current: number, key: string, count: number): number {
  if (count <= 0) return 0;
  if (key === "ArrowRight") return (current + 1) % count;
  if (key === "ArrowLeft") return (current - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return current;
}

export function RunTabBar({
  active,
  onChange,
}: {
  active: RunTabId;
  onChange: (id: RunTabId) => void;
}) {
  const refs = React.useRef<Array<HTMLButtonElement | null>>([]);

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const current = RUN_TABS.findIndex((t) => t.id === active);
    const next = nextTabIndex(current, e.key, RUN_TABS.length);
    if (next === current) return;
    e.preventDefault();
    onChange(RUN_TABS[next].id);
    refs.current[next]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label="Run sections"
      onKeyDown={onKeyDown}
      style={{
        display: "inline-flex",
        gap: 2,
        flexWrap: "wrap",
        background: cssVar("panel"),
        border: `1px solid ${cssVar("border")}`,
        borderRadius: radius.lg,
        padding: 4,
        margin: "0 0 20px",
      }}
    >
      {RUN_TABS.map((tab, i) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            id={`run-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`run-panel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            type="button"
            onClick={() => onChange(tab.id)}
            style={{
              background: selected ? "rgba(var(--accent-rgb), 0.12)" : "transparent",
              color: selected ? cssVar("accent") : cssVar("muted"),
              border: "1px solid transparent",
              borderRadius: radius.md,
              padding: `${space.sm + 1}px ${space.lg + 2}px`,
              fontSize: fontSize.sm,
              fontWeight: selected ? 600 : 500,
              cursor: "pointer",
              transition: "background 0.15s ease, color 0.15s ease",
            }}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

export function RunTabPanel({
  id,
  active,
  children,
  keepMounted = false,
}: {
  id: RunTabId;
  active: boolean;
  children: React.ReactNode;
  keepMounted?: boolean;
}) {
  if (!active && !keepMounted) return null;
  return (
    <div
      role="tabpanel"
      id={`run-panel-${id}`}
      aria-labelledby={`run-tab-${id}`}
      hidden={!active}
    >
      {children}
    </div>
  );
}

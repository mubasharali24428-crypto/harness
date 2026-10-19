"use client";

// Part 56 long-history navigation for the team history log. The stream holds every event in memory
// (the API pages forward only), so "load earlier" widens a render window over what is loaded instead of
// asking the server for older rows; the DOM never holds more than `shown` rows. "Jump to latest" appears
// when you have scrolled away from the end or new events arrived below.
import * as React from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { scrollBehavior } from "@/lib/a11y";
import { formatDateTime, formatNumber, formatTime } from "@/lib/intl";
import type { CollabEvent } from "./collabApi";
import { describeEvent } from "./collabModel";

export const PAGE = 100;
const muted = { color: "var(--muted)" } as const;

/** The newest `shown` events (the list is ascending by seq) and how many older ones stay hidden. */
export function windowEvents<T>(events: T[], shown: number): { rows: T[]; hidden: number } {
  const n = Math.max(0, Math.min(shown, events.length));
  return { rows: events.slice(events.length - n), hidden: events.length - n };
}
export const loadEarlier = (shown: number, total: number, step = PAGE) => Math.min(total, shown + step);
export const nearBottom = (top: number, height: number, full: number, slack = 24) => full - (top + height) <= slack;

export function HistoryLog({ events, initialShown = PAGE, locale }: { events: CollabEvent[]; initialShown?: number; locale?: string }) {
  const [shown, setShown] = useState(initialShown);
  const [atEnd, setAtEnd] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const box = useRef<HTMLOListElement | null>(null);
  const seen = useRef(events.length);
  const { rows, hidden } = windowEvents(events, shown);

  // Follow the end while you are at it; otherwise count what arrived below.
  useLayoutEffect(() => {
    const el = box.current;
    if (events.length > seen.current) {
      if (atEnd && el) el.scrollTop = el.scrollHeight;
      else setUnseen((u) => u + events.length - seen.current);
    }
    seen.current = events.length;
  }, [events.length, atEnd]);
  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight; }, []);

  const jump = () => {
    const el = box.current;
    if (!el) return;
    setShown(PAGE);
    setUnseen(0);
    el.scrollTo?.({ top: el.scrollHeight, behavior: scrollBehavior() });
    (el.lastElementChild as HTMLElement | null)?.focus?.({ preventScroll: true });
  };
  const fmt = { locale };
  return (
    <div data-testid="history-log">
      {hidden > 0 && (
        <p style={{ margin: "0 0 4px" }}>
          <Button size="sm" onClick={() => setShown((s) => loadEarlier(s, events.length))}>
            Load earlier ({formatNumber(Math.min(PAGE, hidden), fmt)} of {formatNumber(hidden, fmt)} hidden)
          </Button>
        </p>
      )}
      <ol
        ref={box} role="log" aria-label="Collaboration history" tabIndex={0}
        onScroll={(e) => { const t = e.currentTarget; const end = nearBottom(t.scrollTop, t.clientHeight, t.scrollHeight); setAtEnd(end); if (end) setUnseen(0); }}
        style={{ margin: 0, paddingLeft: 28, maxHeight: 220, overflow: "auto" }}
      >
        {rows.map((e) => (
          <li key={e.seq} tabIndex={-1}>
            <time dateTime={e.ts} title={formatDateTime(e.ts, fmt)} style={muted}>{formatTime(e.ts, fmt)}</time> {describeEvent(e)}
          </li>
        ))}
        {events.length === 0 && <li style={muted}>Nothing yet.</li>}
      </ol>
      {(!atEnd || unseen > 0) && (
        <p style={{ margin: "4px 0 0" }}>
          <Button size="sm" onClick={jump}>
            Jump to latest{unseen > 0 ? ` (${formatNumber(unseen, fmt)} new)` : ""}
          </Button>
        </p>
      )}
    </div>
  );
}

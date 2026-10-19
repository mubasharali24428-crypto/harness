"use client";

// Part 56: the ONE live-region pair of a page area. `useAnnouncer()` returns `announce` (throttled and
// coalesced, see lib/a11y.ts) and `region`, the two visually hidden elements to render once. Provide
// `announce` to descendants with AnnounceContext so panels do not each own a region.
import * as React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { type Announcer, createAnnouncer } from "@/lib/a11y";

export const AnnounceContext = React.createContext<((text: string, urgent?: boolean) => void) | null>(null);

const hidden: React.CSSProperties = { position: "absolute", width: 1, height: 1, margin: -1, padding: 0, overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0 };

/** A live region only speaks when its text changes: repeat the same sentence by toggling a trailing nbsp. */
const fresh = (prev: string, next: string) => (next === prev ? `${next} ` : next);

export function LiveRegions({ polite, assertive }: { polite: string; assertive: string }) {
  return (
    <>
      <div role="status" aria-live="polite" aria-atomic="true" data-testid="live-polite" style={hidden}>{polite}</div>
      <div role="alert" aria-live="assertive" aria-atomic="true" data-testid="live-assertive" style={hidden}>{assertive}</div>
    </>
  );
}

export function useAnnouncer(): { announce: (text: string, urgent?: boolean) => void; region: React.ReactElement } {
  const [polite, setPolite] = useState("");
  const [assertive, setAssertive] = useState("");
  const last = useRef({ polite: "", assertive: "" });
  const a = useRef<Announcer | null>(null);
  if (a.current === null) {
    a.current = createAnnouncer({
      polite: (t) => { const v = fresh(last.current.polite, t); last.current.polite = v; setPolite(v); },
      assertive: (t) => { const v = fresh(last.current.assertive, t); last.current.assertive = v; setAssertive(v); },
    });
  }
  useEffect(() => () => a.current?.dispose(), []);
  const announce = useMemo(() => (text: string, urgent?: boolean) => a.current?.announce(text, urgent), []);
  return { announce, region: <LiveRegions polite={polite} assertive={assertive} /> };
}

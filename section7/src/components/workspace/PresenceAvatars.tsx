"use client";

// U6 (header slice): who is on this run right now, as initials. Data is the
// existing presence roster (the full roster and mute live in the Advanced pane).

import * as React from "react";
import { getPresence, type PresenceEntry } from "../../../app/projects/[projectId]/runs/[runId]/controlApi";

const HERE = new Set(["online", "idle"]);
const MAX = 4;

/** Pure: the people to show, self last, and how many more there are. */
export function presentPeople(entries: readonly PresenceEntry[]): { shown: PresenceEntry[]; more: number } {
  const here = entries.filter((e) => e.presence && HERE.has(e.presence.status)).sort((a, b) => Number(a.self) - Number(b.self));
  return { shown: here.slice(0, MAX), more: Math.max(0, here.length - MAX) };
}

export function PresenceAvatars({ runId, token, onOpen }: { runId: string; token: string; onOpen: () => void }) {
  const [entries, setEntries] = React.useState<PresenceEntry[]>([]);
  React.useEffect(() => {
    let alive = true;
    const load = () => getPresence(runId, token)
      .then((v) => alive && setEntries(v.mode === "off" ? [] : v.participants))
      .catch(() => {}); // the roster is a courtesy; the run page works without it
    void load();
    const id = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [runId, token]);

  const { shown, more } = presentPeople(entries);
  if (shown.length === 0) return null;
  const names = shown.map((e) => (e.self ? "you" : e.userId)).join(", ");
  return (
    <button type="button" className="ws-presence" data-testid="presence-avatars" onClick={onOpen}
      aria-label={`On this run now: ${names}${more ? ` and ${more} more` : ""}. Open the roster.`}>
      {shown.map((e) => (
        <span key={e.userId} className={`ws-avatar${e.presence?.status === "idle" ? " idle" : ""}`} title={e.announcement} aria-hidden="true">
          {e.userId.replace(/[^a-z0-9]/gi, "").slice(-2).toUpperCase() || "?"}
        </span>
      ))}
      {more > 0 && <span className="ws-avatar more" aria-hidden="true">+{more}</span>}
    </button>
  );
}

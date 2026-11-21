"use client";

// U5: the five keys worth knowing, in the sidebar. Open on a person's first
// visit, closed for good once they say so; always one click away after that.

import * as React from "react";
import { Button } from "@/components/ui";

const SEEN_KEY = "harness.keytour.seen";

export const TOUR_KEYS: ReadonlyArray<{ keys: string[]; does: string }> = [
  { keys: ["⌘K"], does: "Go anywhere: pages, settings, projects" },
  { keys: ["g", "h"], does: "New run" },
  { keys: ["g", "r"], does: "All runs" },
  { keys: ["Enter"], does: "Send the goal or direction (Shift+Enter for a new line)" },
  { keys: ["/"], does: "In the run input: pause, resume or stop the agent" },
];

export function KeyTour() {
  const [open, setOpen] = React.useState(false);
  React.useEffect(() => {
    try {
      setOpen(window.localStorage.getItem(SEEN_KEY) === null);
    } catch {
      // storage blocked: the tour stays closed and still opens on click
    }
  }, []);
  return (
    <details className="key-tour" data-testid="key-tour" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Keyboard</summary>
      <dl>
        {TOUR_KEYS.map((k) => (
          <React.Fragment key={k.does}>
            <dt>{k.keys.map((x) => <kbd key={x}>{x}</kbd>).reduce<React.ReactNode[]>((a, el, i) => (i ? [...a, " then ", el] : [el]), [])}</dt>
            <dd>{k.does}</dd>
          </React.Fragment>
        ))}
      </dl>
      <Button size="sm" testId="key-tour-done" onClick={() => {
        try {
          window.localStorage.setItem(SEEN_KEY, "1");
        } catch {
          // storage blocked: it closes for this visit only
        }
        setOpen(false);
      }}>
        Got it
      </Button>
    </details>
  );
}

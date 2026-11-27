"use client";

// "/" is two surfaces: signed-out visitors get the landing page, a session
// gets the new-run screen (R3): the composer with no run, which proposes a
// plan and opens the run. The server render has no localStorage, so both sides
// start on the landing and the swap happens after mount — no hydration mismatch.

import { useEffect, useState } from "react";
import * as React from "react";
import AppShell from "@/components/AppShell";
import { LandingView } from "@/components/Landing";
import { Composer } from "@/components/workspace/Composer";
import { NeedsYouList } from "@/components/home/NeedsYouList";
import { getProjectId, loadSession, type Session } from "@/lib/auth";
import { PlanGateCard } from "./dashboard/planGate";

export default function Home() {
  const [session, setSession] = useState<Session | null>(null);
  const [gateOpen, setGateOpen] = useState(false);

  useEffect(() => {
    setSession(loadSession());
  }, []);

  if (!session?.token) return <LandingView />;
  return (
    <AppShell active="/">
      <div className="ws-home">
        {/* Operability slice 1: what is waiting on a person comes first; "g h" lands here and
            focuses the list when nothing else holds focus. */}
        <NeedsYouList token={session.token} />
        <h1 className="ws-run-title">New run</h1>
        <p className="ws-run-meta">
          Describe the goal. The agent proposes a plan, and nothing runs before you approve it.
        </p>
        <Composer runId={null} projectId={getProjectId()} token={session.token}
          role={session.principal.role} state={null} />
        {/* The older start card stays one click away: it is the only way to
            attach a screenshot to a goal. */}
        <details className="ws-menu" onToggle={(e) => e.currentTarget.open && setGateOpen(true)}>
          <summary>Start with a screenshot</summary>
          {/* Mounted on first open: the closed card makes no requests. */}
          {gateOpen && <PlanGateCard token={session.token} principal={session.principal} />}
        </details>
      </div>
    </AppShell>
  );
}

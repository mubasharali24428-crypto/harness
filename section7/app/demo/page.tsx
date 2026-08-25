"use client";

// M19-N4 (C10) — the canned-demo entry: a stranger's two-click path to the
// wow moment. One button → POST /demo/seed (idempotent, zero provider keys)
// → land on the COMPLETED run detail page with its real diff, review verdict
// and PR binding. No run id is ever invented client-side: ids come from the
// server's response and are persisted through the SAME accessors every other
// page uses (lib/auth), so AppShell's "Working" link and the Command Center
// light up for the seeded run like any organic one.
//
// HONEST MARKER (plan §C10): the page says REPLAY everywhere it matters —
// copy above the fold, the in-flight label, and the success banner carried to
// the detail page via ?demo=1. This is recorded truth replayed, never a fake
// live run.

import { useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for .tsx.
import * as React from "react";
import { useRouter } from "next/navigation";
import { Brand } from "@/components/ui/Brand";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { displayErrorMessage } from "@/lib/api";
import { setProjectId, setRunId } from "@/lib/auth";
import { seedDemo } from "../../src/lib/demoApi";
import { demoLandingHref } from "./demoEntry";

export default function DemoEntryPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startDemo() {
    const sessionRaw = window.localStorage.getItem("harness.session");
    if (!sessionRaw) {
      // Signed-out strangers bootstrap their identity first (one extra click,
      // still key-free) — then come straight back here via ?next=demo.
      router.push("/login?next=demo");
      return;
    }
    let token: string | null = null;
    try {
      token = (JSON.parse(sessionRaw) as { token?: string }).token ?? null;
    } catch {
      token = null;
    }
    if (!token) {
      router.push("/login?next=demo");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const seeded = await seedDemo(token);
      // Persist through the shared accessors so the whole app sees the same
      // working context (AppShell Working link, Command Center, runs index).
      setProjectId(seeded.projectId);
      setRunId(seeded.runId);
      router.push(demoLandingHref(seeded));
    } catch (err) {
      setError(displayErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      <div className="card login-card" style={{ maxWidth: 520 }}>
        <Brand>HARNESS</Brand>
        <p>
          See a completed agent run — real diff, real review verdict, real pull
          request — in under a minute. No API keys, no setup.
        </p>
        <Notice tone="info" style={{ marginBottom: 8 }}>
          What you&apos;ll see is a REPLAY of a recorded session, clearly labeled as
          such everywhere it appears. Nothing live is executed.
        </Notice>
        <Button
          variant="primary"
          size="md"
          testId="demo-seed-button"
          disabled={busy}
          loading={busy}
          loadingText="Preparing replay…"
          onClick={startDemo}
          style={{ width: "100%" }}
        >
          Try the demo
        </Button>
        {error && (
          <Notice tone="danger" style={{ marginTop: 8 }} testId="demo-error">
            {error}
          </Notice>
        )}
      </div>
    </div>
  );
}

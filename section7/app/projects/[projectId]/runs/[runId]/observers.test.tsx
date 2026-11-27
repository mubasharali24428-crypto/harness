// Task 19: external observers list (node env, static render) + client URL.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { type AdapterLease, ObserversView, listAdapters } from "./observersSection";

afterEach(() => vi.unstubAllGlobals());

const lease = (over: Partial<AdapterLease> = {}): AdapterLease => ({
  leaseId: "l1", agentId: "ext-coder", protocolVersion: "1", capabilities: ["read_events"],
  requestedScope: ["src/api"], mode: "observer", authority: [], expiresAt: "2026-11-25T12:00:00+00:00",
  expired: false, registeredBy: "lead", ...over,
});

describe("external observers", () => {
  it("empty state", () => {
    expect(renderToStaticMarkup(<ObserversView adapters={[]} />)).toContain("No external agents are observing this run.");
  });
  it("shows scope as asked-for, never as granted, and the lease expiry", () => {
    const h = renderToStaticMarkup(<ObserversView adapters={[lease()]} />);
    expect(h).toContain("observes only (no authority)");
    expect(h).toContain("Asked for (not granted): src/api.");
    expect(h).toContain("Lease until");
    expect(renderToStaticMarkup(<ObserversView adapters={[lease({ expired: true })]} />)).toContain("Lease expired");
  });
  it("lists from the run-scoped route", async () => {
    const fn = vi.fn(async () => new Response(JSON.stringify({ adapters: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fn);
    await listAdapters("r 1", "t");
    expect((fn.mock.calls[0] as unknown as [string])[0]).toMatch(/\/api\/v1\/orchestrator\/runs\/r%201\/adapters$/);
  });
});

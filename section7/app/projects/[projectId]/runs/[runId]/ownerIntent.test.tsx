// Part 40/42 UI (A5): session owner panel, intent form, run history labels (A6). Node env, no jsdom.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "@/lib/api";
import { stepLabel } from "../../../../runs/runsApi";
import { friendlyError, getRunSession, putIntent, transferOwner } from "./controlApi";
import { IntentForm } from "./intentSection";
import { OwnerPanel } from "./ownerSection";

afterEach(() => vi.unstubAllGlobals());
const noop = () => undefined;

function stub(responses: unknown[]) {
  const fn = vi.fn(async () => {
    const body = responses.length > 1 ? responses.shift() : responses[0];
    return { ok: true, status: 200, text: async () => JSON.stringify(body) };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
const call = (fn: ReturnType<typeof stub>, i = 0) => {
  const [url, init] = fn.mock.calls[i] as unknown as [string, RequestInit];
  return { url, init, body: init.body ? JSON.parse(init.body as string) : undefined };
};

const session = { id: "s1", title: "Billing fix", state: "active", ownerUserId: "alice", yourRole: "owner" };
const members = [
  { userId: "alice", kind: "user", displayName: "Alice", status: "active", role: "owner" },
  { userId: "bob", kind: "user", displayName: "Bob", status: "active", role: "participant" },
  { userId: null, kind: "agent", displayName: "Coder", status: "active", role: "participant" },
];

describe("session owner panel", () => {
  it("names the owner and offers only other human members", () => {
    const html = renderToStaticMarkup(<OwnerPanel session={session} members={members} me="alice" canTransfer busy={false} onTransfer={noop} />);
    expect(html).toContain("alice");
    expect(html).toContain("(you)");
    expect(html).toContain(">Bob<");
    expect(html).not.toContain("Coder");
    expect(html).toContain("Transfer ownership");
  });
  it("shows no transfer form without the right", () => {
    const html = renderToStaticMarkup(<OwnerPanel session={session} members={members} me="bob" canTransfer={false} busy={false} onTransfer={noop} />);
    expect(html).toContain("alice");
    expect(html).not.toContain("Transfer ownership");
  });
  it("finds the run's session, then reads it", async () => {
    const fn = stub([{ items: [{ id: "s1" }] }, session]);
    expect((await getRunSession("r 1", "tok"))?.id).toBe("s1");
    expect(call(fn).url).toContain("/api/v1/sessions?runId=r%201&limit=1");
    expect(call(fn, 1).url).toContain("/api/v1/sessions/s1");
  });
  it("a run in no session has no owner panel", async () => {
    stub([{ items: [] }]);
    expect(await getRunSession("r1", "tok")).toBeNull();
  });
  it("sends the owner it saw so a stale hand-over is refused", async () => {
    const fn = stub([session]);
    await transferOwner("s1", "tok", "bob", "alice");
    expect(call(fn).body).toEqual({ toUserId: "bob", expectedOwnerUserId: "alice" });
  });
  it("a stale owner reads as plain language", () => {
    expect(friendlyError(new ApiError("raw", "STALE_CLIENT_STATE", 409))).toMatch(/owner changed/i);
  });
});

describe("intent form", () => {
  const view = { intent: { version: 3, goal: "Fix billing", scopePaths: ["src", "docs"], permissions: null, updatedBy: "alice", updatedAt: null }, openConflicts: 1 };
  it("shows version, scope lines and open conflicts", () => {
    const html = renderToStaticMarkup(<IntentForm view={view} canEdit busy={false} onSave={noop} />);
    expect(html).toContain("version 3");
    expect(html).toContain("src\ndocs");
    expect(html).toContain("1 conflicting edit waiting");
    expect(html).toContain("Save intent");
  });
  it("says the whole workspace is open when no path is listed", () => {
    const open = { ...view, intent: { ...view.intent, scopePaths: [] } };
    expect(renderToStaticMarkup(<IntentForm view={open} canEdit busy={false} onSave={noop} />)).toContain("whole workspace is open");
  });
  it("is read-only without the right", () => {
    const html = renderToStaticMarkup(<IntentForm view={view} canEdit={false} busy={false} onSave={noop} />);
    expect(html).not.toContain("Save intent");
    expect(html).toContain("disabled");
  });
  it("saves against the version it showed", async () => {
    const fn = stub([{}]);
    await putIntent("r1", "tok", 3, { goal: "g", scopePaths: ["src"] });
    const c = call(fn);
    expect(c.init.method).toBe("PUT");
    expect(c.body).toMatchObject({ expectedVersion: 3, goal: "g", scopePaths: ["src"] });
    expect(typeof c.body.requestId).toBe("string");
  });
});

describe("run history tells a stop from a pause and a failure", () => {
  it("labels", () => {
    expect(stepLabel("paused")).toBe("Paused");
    expect(stepLabel("paused", "user_requested")).toBe("Paused");
    expect(stepLabel("paused", "user_requested_stop")).toBe("Stopped by user");
    expect(stepLabel("engine_failed")).toBe("Engine failed");
    expect(stepLabel("engine_failed", "timeout: 600s")).toBe("Engine failed: timeout: 600s");
  });
});

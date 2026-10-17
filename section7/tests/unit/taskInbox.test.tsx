import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { TaskInboxView, safeHref, preview } from "../../src/components/TaskInbox";
import { createIntakeApi, type Intake } from "../../src/lib/intakeApi";

const item = (over: Partial<Intake> = {}): Intake => ({
  id: "i1", status: "pending_approval", provider: "github", kind: "issue", title: "Fix login",
  body: "b", url: "https://github.com/o/r/issues/1", actor: "alice", trigger: "label:harness", ...over,
});
const render = (items: Intake[], canDecide = true) =>
  renderToStaticMarkup(<TaskInboxView items={items} canDecide={canDecide} busyId={null} error={null} notice={null} onDecide={() => {}} />);

describe("TaskInbox", () => {
  it("renders pending items with labelled 44px-target actions and states approval starts no run", () => {
    const html = render([item()]);
    expect(html).toContain('aria-label="Approve Fix login"');
    expect(html).toContain("1 waiting for a decision");
    expect(html).toContain("does not start a run");
    expect(html).toContain("min-height:44px");
  });
  it("escapes hostile text and drops non-http links", () => {
    const html = render([item({ title: "<script>alert(1)</script>", url: "javascript:alert(1)" })]);
    expect(html).not.toContain("<script>alert");
    expect(html).not.toContain("javascript:");
    expect(safeHref("javascript:x")).toBeNull();
    expect(safeHref("https://a.b")).toBe("https://a.b");
  });
  it("hides decision buttons for read-only roles and for already-decided items", () => {
    expect(render([item()], false)).not.toContain("Approve Fix login");
    expect(render([item({ status: "approved" })])).not.toContain("Approve Fix login");
  });
  it("truncates long bodies", () => expect(preview("x".repeat(500)).length).toBe(160));
  it("api client sends bearer, maps errors, never calls without a token", async () => {
    const seen: string[] = [];
    const api = createIntakeApi({
      base: "http://svc", getToken: () => "tok",
      fetchImpl: (async (u: string, init: RequestInit) => {
        seen.push(`${init.method} ${u} ${(init.headers as Record<string, string>).authorization}`);
        return new Response(JSON.stringify({ error: { code: "ROLE_DENIED", message: "nope" } }), { status: 403 });
      }) as unknown as typeof fetch,
    });
    await expect(api.decide("i 1", true)).rejects.toMatchObject({ status: 403 });
    expect(seen[0]).toBe("POST http://svc/api/v1/developer-workflow/intake/intakes/i%201/approve Bearer tok");
    await expect(createIntakeApi({ base: "http://x", getToken: () => null }).list()).rejects.toMatchObject({ status: 401 });
  });
});

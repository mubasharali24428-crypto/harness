import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { executeAction } from "../src/actions.js";
import { PreviewRegistry, startStaticPreview } from "../src/preview.js";
import { replayLog } from "../src/replay.js";
import { sanitizeHeaders, sanitizeUrl } from "../src/diagnostics.js";
import { mintFor, newManager, startFixturePair, TEST_SECRET, testIdentity } from "./support/harness.js";
import type { BrowserAction } from "../src/types.js";

describe("S2 browser diagnostics, mobile, replay, visual", () => {
  let pair: Awaited<ReturnType<typeof startFixturePair>>;
  let mgr: Awaited<ReturnType<typeof newManager>>;
  const baselineDir = mkdtempSync(join(tmpdir(), "a3-baselines-"));

  beforeAll(async () => { pair = await startFixturePair(); mgr = await newManager(); });
  afterAll(async () => { await mgr.manager.dispose(); mgr.cleanup(); await pair.close(); });

  async function open(runId: string, opts: { profile?: string } = {}) {
    const identity = testIdentity({ runId });
    const session = await mgr.manager.open(identity, { allowedOrigins: [pair.siteA.origin], ...opts });
    const cap = () => mintFor(identity, [pair.siteA.origin]);
    const act = (a: object) =>
      executeAction(session, { ...a, capabilityToken: cap() } as BrowserAction, { capabilitySecret: TEST_SECRET, baselineDir });
    return { session, act };
  }

  it("captures network+console with secrets removed, and blocks subresource egress", async () => {
    const { session, act } = await open("diag");
    await act({ kind: "navigate", url: `${pair.siteA.origin}/diag` });
    await act({ kind: "click", target: { by: "selector", selector: "#go" } });
    await act({ kind: "waitFor", urlContains: "/diag", timeoutMs: 1500 });
    await new Promise((r) => setTimeout(r, 400));
    const diag = await act({ kind: "diagnostics" });
    const har = JSON.stringify(session.diagnostics.toHar());
    const dump = JSON.stringify(diag.result) + har;
    for (const secret of ["SUPERSECRET", "SUPERSECRETBEARER", "COOKIESECRET", "APISECRET", "HDRSECRET", "hunter2", "abcdef1234567890abcdef"]) {
      expect(dump, `leaked ${secret}`).not.toContain(secret);
    }
    const net = (diag.result as { network: { url: string; blockedByPolicy?: boolean; status: number | null }[] }).network;
    expect(net.some((n) => n.url.includes("/api/ping") && n.url.includes("token=%5BREDACTED%5D") && n.status === 200)).toBe(true);
    const blocked = net.find((n) => n.url.includes("/exfil"));
    expect(blocked?.blockedByPolicy).toBe(true);
    const cons = (diag.result as { console: { text: string }[] }).console.map((c) => c.text).join("|");
    expect(cons).toContain("[REDACTED]");
  });

  it("sanitizers handle fragments, credentials and unknown headers", () => {
    expect(sanitizeUrl("https://u:p@h.example/x?api_key=1&ok=2#access_token=zzz")).toBe("https://h.example/x?api_key=%5BREDACTED%5D&ok=2");
    expect(sanitizeHeaders({ Authorization: "Bearer x", "X-Custom": "v", "Content-Type": "text/html" })).toEqual({
      authorization: "[REDACTED]", "x-custom": "[REDACTED]", "content-type": "text/html",
    });
  });

  it("emulates a mobile profile: touch, DPR, tap, and refuses tap on desktop", async () => {
    const { act, session } = await open("mobile", { profile: "iphone-13" });
    const nav = await act({ kind: "navigate", url: `${pair.siteA.origin}/pwa` });
    expect(nav.observation!.viewport).toMatchObject({ width: 390, deviceScaleFactor: 3 });
    const tap = await act({ kind: "tap", target: { by: "selector", selector: "#tap" }, expect: { selectorVisible: "#count" } });
    expect(tap.status).toBe("succeeded");
    expect(await session.page.textContent("#count")).toBe("1");
    const pwa = await act({ kind: "pwaCheck" });
    expect(pwa.result).toMatchObject({ manifestLinked: true, viewportMeta: true, manifest: { name: "Fixture PWA", iconCount: 1 } });

    const desk = await open("desk");
    await desk.act({ kind: "navigate", url: `${pair.siteA.origin}/pwa` });
    const denied = await desk.act({ kind: "tap", target: { by: "selector", selector: "#tap" } });
    expect(denied.status).toBe("unsupported");
    await expect(mgr.manager.open(testIdentity({ runId: "bad" }), { allowedOrigins: [pair.siteA.origin], profile: "nokia" })).rejects.toThrow(/unknown device profile/);
  });

  it("records a redacted replay log and detects a diverging replay", async () => {
    const a = await open("rec");
    await a.act({ kind: "navigate", url: `${pair.siteA.origin}/form` });
    await a.act({ kind: "type", target: { by: "selector", selector: "#note" }, text: "s3cret-value" });
    await a.act({ kind: "click", target: { by: "role", role: "button", name: "Send local" } });
    const log = a.session.replayLog;
    expect(log.map((e) => e.status)).toEqual(["succeeded", "succeeded", "succeeded"]);
    expect(JSON.stringify(log)).not.toContain("s3cret-value");
    expect(JSON.stringify(log)).not.toContain("bcap1");
    expect(log[0]).toMatchObject({ viewport: { width: 1280, deviceScaleFactor: 1 } });

    const b = await open("rep");
    const run = (x: BrowserAction) => executeAction(b.session, { ...x, capabilityToken: mintFor(testIdentity({ runId: "rep" }), [pair.siteA.origin]) } as BrowserAction, { capabilitySecret: TEST_SECRET });
    const same = await replayLog(log, run, { textProvider: () => "s3cret-value" });
    expect(same).toEqual({ divergences: [], replayed: 3 });

    const skipped = await replayLog(log, run);
    expect(skipped.divergences.map((d) => d.kind)).toContain("skipped");

    // Deliberate change: the target button no longer exists on the replayed page.
    const c = await open("rep2");
    const tampered = log.map((e, i) => (i === 0 ? { ...e, action: { ...e.action, url: `${pair.siteA.origin}/tall` } } : e));
    const run2 = (x: BrowserAction) => executeAction(c.session, { ...x, capabilityToken: mintFor(testIdentity({ runId: "rep2" }), [pair.siteA.origin]) } as BrowserAction, { capabilitySecret: TEST_SECRET });
    const diverged = await replayLog(tampered, run2, { textProvider: () => "s3cret-value" });
    expect(diverged.divergences.some((d) => d.kind === "status" || d.kind === "domDigest")).toBe(true);
  });

  it("visual regression: new != pass, approve, match, deliberate change fails with diff, masks clocks", async () => {
    const { act } = await open("vis");
    const go = (q = "") => act({ kind: "navigate", url: `${pair.siteA.origin}/visual${q}` });
    await go();
    const fresh = await act({ kind: "visualCheck", name: "home", mask: ["#clock"] });
    expect(fresh.status).toBe("failed");
    expect(fresh.error?.message).toContain("new");

    const approved = await act({ kind: "visualCheck", name: "home", mask: ["#clock"], approveAs: "reviewer@example.com" });
    expect(approved.result).toMatchObject({ status: "baselineApproved" });

    // Determinism: reload (different Date.now in #clock, animation running) still matches.
    await go();
    const again = await act({ kind: "visualCheck", name: "home", mask: ["#clock"] });
    expect(again.status).toBe("succeeded");

    await go("?color=%23d93025");
    const changed = await act({ kind: "visualCheck", name: "home", mask: ["#clock"] });
    expect(changed.status).toBe("failed");
    expect(changed.error?.message).toMatch(/diff/);
    expect(JSON.stringify(changed.result)).toContain("diffArtifactIds");
    // Tolerant threshold lets a tiny budget through only when asked.
    const lenient = await act({ kind: "visualCheck", name: "home", mask: ["#clock"], maxDiffRatio: 1 });
    expect(lenient.status).toBe("succeeded");

    const traversal = await act({ kind: "visualCheck", name: "../../etc/passwd" });
    expect(traversal.status).toBe("failed");
    expect(traversal.error?.code).toBe("BAD_REQUEST");
  });

  it("agent-invokable e2e scenario: stops at first failing step and reports it", async () => {
    const { act } = await open("e2e");
    const pass = await act({ kind: "e2e", steps: [
      { kind: "navigate", url: `${pair.siteA.origin}/form` },
      { kind: "type", target: { by: "selector", selector: "#note" }, text: "hi", expect: { valueEquals: { target: { by: "selector", selector: "#note" }, equals: "hi" } } },
      { kind: "click", target: { by: "role", role: "button", name: "Send local" }, expect: { urlContains: "/submitted" } },
    ] });
    expect(pass.status).toBe("succeeded");
    expect((pass.result as { passed: number }).passed).toBe(3);

    const fail = await act({ kind: "e2e", steps: [
      { kind: "navigate", url: `${pair.siteA.origin}/form` },
      { kind: "click", target: { by: "selector", selector: "#nope" } },
      { kind: "navigate", url: `${pair.siteA.origin}/tall` },
    ] });
    expect(fail.status).toBe("failed");
    const steps = (fail.result as { details: { steps: unknown[] } }).details.steps;
    expect(steps).toHaveLength(2); // third step never ran
  });
});

describe("S2 governed preview", () => {
  const reg = new PreviewRegistry({ minPort: 3000, maxPort: 9000, reservedPorts: [5432, 8080] });
  it("only registered loopback ports for the owning team; metadata/public denied", () => {
    expect(() => reg.assertOriginsAllowed("t1", ["http://127.0.0.1:3000"], [])).toThrow(/not a registered preview/);
    expect(reg.register("t1", 3000)).toBe("http://127.0.0.1:3000");
    expect(() => reg.assertOriginsAllowed("t1", ["http://127.0.0.1:3000"], [])).not.toThrow();
    expect(() => reg.assertOriginsAllowed("t2", ["http://127.0.0.1:3000"], [])).toThrow();
    expect(() => reg.register("t1", 5432)).toThrow(/reserved/);
    expect(() => reg.register("t1", 22)).toThrow(/range/);
    expect(() => reg.assertOriginsAllowed("t1", ["http://169.254.169.254"], [])).toThrow();
    expect(() => reg.assertOriginsAllowed("t1", ["https://example.com"], [])).toThrow(/allowlist/);
    expect(() => reg.assertOriginsAllowed("t1", ["https://example.com"], ["https://example.com"])).not.toThrow();
  });

  it("static preview serves a build dir and refuses traversal", async () => {
    const dir = mkdtempSync(join(tmpdir(), "a3-prev-"));
    mkdirSync(join(dir, "app"));
    writeFileSync(join(dir, "app", "index.html"), "<h1>built</h1>");
    writeFileSync(join(dir, "secret.txt"), "outside");
    const p = await startStaticPreview(join(dir, "app"));
    try {
      expect(await (await fetch(`${p.origin}/`)).text()).toContain("built");
      expect((await fetch(`${p.origin}/%2e%2e/secret.txt`)).status).toBe(404);
    } finally { await p.close(); }
  });
});

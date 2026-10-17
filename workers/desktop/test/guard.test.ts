import { writeFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { DesktopController, DesktopError, MacOSAdapter, type DesktopAdapter, type DesktopGrant } from "../src/index.js";

// Test-only recording stand-in: proves guard logic, NOT real OS behaviour.
function recorder(ok = true) {
  const calls: unknown[] = [];
  const adapter: DesktopAdapter = {
    name: "recorder",
    probe: async () => ok ? { ok, reasons: [], display: { id: "d", width: 1000, height: 800, scale: 2 } } : { ok, reasons: ["no screen recording permission"] },
    capture: async (_r, out) => void writeFileSync(out, Buffer.from("png-bytes")),
    perform: async (op) => void calls.push(op),
  };
  return { adapter, calls };
}
const grant = (over: Partial<DesktopGrant> = {}): DesktopGrant => ({
  grantId: "g", teamId: "t", runId: "r", capabilities: ["capture", "input"],
  region: { x: 100, y: 100, width: 400, height: 300 }, expiresAtMs: 10_000, ...over,
});
const code = async (p: Promise<unknown>) => p.then(() => "ok", (e: DesktopError) => e.code);

describe("DesktopController", () => {
  it("fails closed with no grant, wrong run, expired grant, missing capability", async () => {
    const { adapter } = recorder();
    expect(await code(new DesktopController(adapter, null).observe("t", "r"))).toBe("NO_GRANT");
    expect(await code(new DesktopController(adapter, grant(), () => 0).observe("t", "other"))).toBe("GRANT_SCOPE");
    expect(await code(new DesktopController(adapter, grant(), () => 20_000).observe("t", "r"))).toBe("GRANT_EXPIRED");
    expect(await code(new DesktopController(adapter, grant({ capabilities: ["capture"] }), () => 0).act("t", "r", "x", { kind: "key", combo: "enter" }))).toBe("GRANT_SCOPE");
  });

  it("reports UNSUPPORTED when the OS adapter cannot run", async () => {
    expect(await code(new DesktopController(recorder(false).adapter, grant(), () => 0).observe("t", "r"))).toBe("UNSUPPORTED");
  });

  it("transforms image pixels to logical points on a 2x display", async () => {
    const { adapter, calls } = recorder();
    const c = new DesktopController(adapter, grant(), () => 1000);
    const obs = await c.observe("t", "r");
    expect(obs.imageSize).toEqual({ width: 800, height: 600 });
    await c.act("t", "r", obs.observationId, { kind: "click", x: 200, y: 100, space: "image" });
    expect(calls[0]).toMatchObject({ kind: "click", x: 200, y: 150, space: "point" }); // 100+200/2, 100+100/2
  });

  it("rejects out-of-region targets, stale/superseded observations and unobserved acts", async () => {
    let t = 1000;
    const { adapter, calls } = recorder();
    const c = new DesktopController(adapter, grant({ expiresAtMs: 1e9 }), () => t);
    expect(await code(c.act("t", "r", "none", { kind: "key", combo: "enter" }))).toBe("NEEDS_OBSERVATION");
    const first = await c.observe("t", "r");
    expect(await code(c.act("t", "r", first.observationId, { kind: "click", x: 50, y: 50, space: "point" }))).toBe("OUT_OF_REGION");
    expect(await code(c.act("t", "r", first.observationId, { kind: "click", x: 0, y: 0, space: "image" }))).toBe("ok"); // region origin
    const second = await c.observe("t", "r");
    expect(await code(c.act("t", "r", first.observationId, { kind: "key", combo: "enter" }))).toBe("STALE_OBSERVATION");
    t += 6000;
    expect(await code(c.act("t", "r", second.observationId, { kind: "key", combo: "enter" }))).toBe("STALE_OBSERVATION");
    expect(calls).toHaveLength(1);
  });

  it("pauses agent input during human takeover and invalidates the observation on handback", async () => {
    const { adapter, calls } = recorder();
    const c = new DesktopController(adapter, grant({ expiresAtMs: 1e9 }), () => 1000);
    const obs = await c.observe("t", "r");
    c.takeOver();
    expect(await code(c.act("t", "r", obs.observationId, { kind: "type", text: "x" }))).toBe("HUMAN_CONTROL");
    c.handBack();
    expect(await code(c.act("t", "r", obs.observationId, { kind: "type", text: "x" }))).toBe("NEEDS_OBSERVATION");
    expect(calls).toHaveLength(0);
  });

  it("validates key combos and text length", async () => {
    const c = new DesktopController(recorder().adapter, grant({ expiresAtMs: 1e9 }), () => 1000);
    const obs = await c.observe("t", "r");
    expect(await code(c.act("t", "r", obs.observationId, { kind: "key", combo: 'a"; do shell script "x' }))).toBe("BAD_REQUEST");
    expect(await code(c.act("t", "r", obs.observationId, { kind: "type", text: "" }))).toBe("BAD_REQUEST");
  });
});

describe("MacOSAdapter (command construction with injected exec; no real OS calls)", () => {
  it("probe is not-ok when the display cannot be read", async () => {
    const a = new MacOSAdapter(async () => { throw new Error("denied"); });
    const p = await a.probe();
    expect(p.ok).toBe(false);
    expect(p.reasons.join()).toContain("denied");
  });

  it("passes typed text as argv, never interpolated into script source", async () => {
    const seen: string[][] = [];
    const a = new MacOSAdapter(async (_c, args) => { seen.push(args); return { stdout: "" }; });
    await a.perform({ kind: "type", text: 'x" & (do shell script "id") & "' });
    expect(seen[0]![1]).not.toContain("do shell script");
    expect(seen[0]![2]).toContain("do shell script"); // only as data in argv slot
  });

  it("rejects non-numeric coordinates", async () => {
    const a = new MacOSAdapter(async () => ({ stdout: "" }));
    await expect(a.perform({ kind: "click", x: "1; rm", y: 2 })).rejects.toThrow(/coordinate/);
  });
});

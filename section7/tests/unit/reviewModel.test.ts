import { describe, expect, it } from "vitest";
import {
  type ChangeSet,
  flatHunks,
  keyIntent,
  moveIndex,
  summarize,
  withDecision,
} from "../../src/lib/reviewModel";
import { createDevWorkflowApi } from "../../src/lib/devWorkflowApi";
import { ApiError } from "../../src/lib/api";

const h = (id: string) => ({ id, oldStart: 1, oldCount: 1, newStart: 1, newCount: 1, lines: [] });
const cs = (over: Partial<ChangeSet> = {}): ChangeSet => ({
  changeSetId: "cs1",
  sessionId: "s1",
  title: "t",
  status: "open",
  files: [
    { path: "a.py", baseSha256: "x", drift: "current", hunks: [h("h0"), h("h1")], decisions: {} },
    { path: "b.py", baseSha256: "y", drift: "current", hunks: [h("h0")], decisions: {} },
  ],
  ...over,
});

describe("summarize", () => {
  it("counts pending by default and blocks apply until a hunk is accepted", () => {
    const s = summarize(cs(), true);
    expect(s).toMatchObject({ accepted: 0, rejected: 0, pending: 3, canApply: false });
    expect(s.blocker).toMatch(/Accept at least one/);
  });
  it("allows apply with an accepted hunk even when others stay pending/rejected", () => {
    let c = withDecision(cs(), "a.py", "h0", "accept");
    c = withDecision(c, "a.py", "h1", "reject");
    const s = summarize(c, true);
    expect(s).toMatchObject({ accepted: 1, rejected: 1, pending: 1, canApply: true, blocker: null });
  });
  it("read-only roles can never apply", () => {
    const c = withDecision(cs(), "a.py", "h0", "accept");
    const s = summarize(c, false);
    expect(s.canApply).toBe(false);
    expect(s.blocker).toMatch(/read-only/);
  });
  it("stale files block apply and are named", () => {
    const c = cs();
    c.files[1].drift = "stale";
    const s = summarize(withDecision(c, "a.py", "h0", "accept"), true);
    expect(s.canApply).toBe(false);
    expect(s.staleFiles).toEqual(["b.py"]);
  });
  it("an applied change set does not report drift (workspace legitimately moved on)", () => {
    const c = cs({ status: "applied" });
    c.files[0].drift = "stale";
    expect(summarize(c, true).staleFiles).toEqual([]);
  });
  it("applied/cancelled change sets cannot be applied again", () => {
    const c = withDecision(cs({ status: "applied" }), "a.py", "h0", "accept");
    expect(summarize(c, true).canApply).toBe(false);
  });
});

describe("keyboard", () => {
  it("maps vim-style keys and ignores modified or text-entry keys", () => {
    expect(keyIntent({ key: "j" })).toEqual({ type: "move", delta: 1 });
    expect(keyIntent({ key: "k" })).toEqual({ type: "move", delta: -1 });
    expect(keyIntent({ key: "a" })).toEqual({ type: "decide", decision: "accept" });
    expect(keyIntent({ key: "r" })).toEqual({ type: "decide", decision: "reject" });
    expect(keyIntent({ key: "a", ctrlKey: true })).toBeNull();
    expect(keyIntent({ key: "a", metaKey: true })).toBeNull();
    expect(keyIntent({ key: "a", targetIsTextEntry: true })).toBeNull();
    expect(keyIntent({ key: "Escape", targetIsTextEntry: true })).toEqual({ type: "blur" });
    expect(keyIntent({ key: "x" })).toBeNull();
  });
  it("moveIndex clamps and handles empty lists", () => {
    expect(moveIndex(-1, 1, 3)).toBe(0);
    expect(moveIndex(-1, -1, 3)).toBe(2);
    expect(moveIndex(2, 1, 3)).toBe(2);
    expect(moveIndex(0, -1, 3)).toBe(0);
    expect(moveIndex(0, 1, 0)).toBe(-1);
  });
  it("flatHunks crosses files in order", () => {
    expect(flatHunks(cs()).map((x) => `${x.path}#${x.hunkId}`)).toEqual(["a.py#h0", "a.py#h1", "b.py#h0"]);
  });
});

describe("devWorkflowApi", () => {
  const ok = (body: unknown, status = 200) =>
    (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

  it("sends bearer, camelCase bodies, and surfaces the frozen error envelope", async () => {
    let seen: { url: string; init: RequestInit } | undefined;
    const api = createDevWorkflowApi({
      base: "http://dw/",
      getToken: () => "tok",
      fetchImpl: (async (url: string, init: RequestInit) => {
        seen = { url, init };
        return new Response(JSON.stringify({ error: { code: "STALE_BASE", message: "changed" } }), { status: 409 });
      }) as unknown as typeof fetch,
    });
    await expect(api.apply("cs1", "k1")).rejects.toMatchObject({ code: "STALE_BASE", status: 409 });
    expect(seen?.url).toBe("http://dw/api/v1/developer-workflow/change-sets/cs1/apply");
    expect((seen?.init.headers as Record<string, string>).authorization).toBe("Bearer tok");
    expect(JSON.parse(seen?.init.body as string)).toEqual({ idempotencyKey: "k1" });
  });
  it("fails closed without a token and when unreachable — never a fake success", async () => {
    const noTok = createDevWorkflowApi({ getToken: () => null, fetchImpl: ok({}) });
    await expect(noTok.getChangeSet("x")).rejects.toBeInstanceOf(ApiError);
    const down = createDevWorkflowApi({
      getToken: () => "t",
      fetchImpl: (async () => {
        throw new Error("ECONNREFUSED");
      }) as unknown as typeof fetch,
    });
    await expect(down.cancel("x")).rejects.toMatchObject({ code: "UPSTREAM_UNREACHABLE" });
  });
  it("url-encodes ids", async () => {
    let url = "";
    const api = createDevWorkflowApi({
      base: "http://dw",
      getToken: () => "t",
      fetchImpl: (async (u: string) => {
        url = u;
        return new Response("{}", { status: 200 });
      }) as unknown as typeof fetch,
    });
    await api.getChangeSet("a/../b");
    expect(url).toContain("a%2F..%2Fb");
  });
});

// Part 46 client rules. The reducer cases are the ones section4/tests/unit/test_delivery_reducer.py
// uses as the specification of the port; the randomized test replays its loss/duplicate/swap scenario
// against an in-memory log that interleaves another run (so global seq has holes).
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  type Frame, FrameReducer, closePolicy, cooldownSeconds, createStatusAnnouncer, isStale, mutationError,
  normalizeHistory, parseFrame, pollIntervalMs, reconnectDelayMs, statusText,
} from "./realtimeCore";

const f = (seq: number, prevSeq?: number | null): Frame => ({ seq, prevSeq, actor: "u", kind: "participant.joined", payload: {}, ts: "t" });
const chain = (...seqs: number[]): Frame[] => seqs.map((s, i) => f(s, i === 0 ? null : seqs[i - 1]));
const seqsOf = (frames: Frame[]) => frames.map((x) => x.seq);

describe("FrameReducer (port of delivery.Reducer)", () => {
  it("delivers an ordered stream at once and moves the cursor", () => {
    const r = new FrameReducer();
    expect(chain(4, 9, 10).map((x) => seqsOf(r.push(x)))).toEqual([[4], [9], [10]]);
    expect(r.cursor).toBe(10);
    expect(r.gap()).toBe(false);
  });

  it("drops and counts repeats, also after a reconnect overlap", () => {
    const r = new FrameReducer();
    const frames = chain(4, 9, 10);
    frames.forEach((x) => r.push(x));
    const out = [...frames, ...frames.slice(1)].map((x) => r.push(x));
    expect(out).toEqual([[], [], [], [], []]);
    expect(r.duplicates).toBe(5);
    expect(r.cursor).toBe(10);
  });

  it("holds a frame until its predecessor arrives, then releases the run in order", () => {
    const r = new FrameReducer();
    const [f4, f9, f10, f12] = chain(4, 9, 10, 12);
    expect(seqsOf(r.push(f4))).toEqual([4]);
    expect(r.push(f12)).toEqual([]);
    expect(r.push(f10)).toEqual([]);
    expect(r.pending()).toBe(2);
    expect(r.gap(500)).toBe(false);
    expect(seqsOf(r.push(f9))).toEqual([9, 10, 12]);
    expect(r.cursor).toBe(12);
    expect(r.pending()).toBe(0);
  });

  it("a missing event is a gap after the wait or when the buffer is full", () => {
    const slow = new FrameReducer(0, 100, 3000);
    const [f1, , f3] = chain(1, 2, 3);
    slow.push(f1, 0);
    slow.push(f3, 1000);
    expect(slow.gap(3900)).toBe(false);
    expect(slow.gap(4000)).toBe(true);
    const full = new FrameReducer(0, 2, 60_000);
    full.push(chain(1, 2, 3, 4, 5)[4], 0);
    full.push(f(6, 5), 0);
    expect(full.gap(0)).toBe(true);
  });

  it("nothing visible between cursor and frame means it is not held; legacy pages are ordered by cursor", () => {
    expect(seqsOf(new FrameReducer(7).push(f(9, 5)))).toEqual([9]);
    expect(seqsOf(new FrameReducer().push(f(3, null)))).toEqual([3]);
    const legacy = new FrameReducer();
    expect(seqsOf(legacy.push(f(5)))).toEqual([5]);
    expect(legacy.push(f(4))).toEqual([]);
  });

  it("a held frame that a backfill also delivers is applied once", () => {
    const r = new FrameReducer();
    expect(r.push(f(10, 9), 0)).toEqual([]);
    const got: Frame[] = [];
    for (const x of chain(8, 9, 10)) got.push(...r.push(x, 1000));
    expect(seqsOf(got)).toEqual([8, 9, 10]);
    expect(r.duplicates).toBe(1);
    expect(r.pending()).toBe(0);
  });

  it("reset (cursorAhead) forgets cursor and buffer", () => {
    const r = new FrameReducer();
    r.push(f(5, null));
    r.push(f(9, 8));
    r.reset(0);
    expect(r.cursor).toBe(0);
    expect(r.pending()).toBe(0);
    expect(seqsOf(r.push(f(1, null)))).toEqual([1]);
  });
});

// A seeded generator (the python test uses random.Random(seed)).
const rng = (seed: number) => { let s = seed * 2654435761 % 4294967296 || 1; return () => (s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296; };

/** In-memory log: every third event belongs to another run, which opens holes in the global seq. */
function seedLog(n: number): { run: Frame[]; truth: number[] } {
  const run: Frame[] = [];
  let prev: number | null = null;
  for (let i = 1; i <= n; i++) {
    if (i % 3 === 0) continue; // another run's event
    run.push(f(i, prev));
    prev = i;
  }
  return { run, truth: seqsOf(run) };
}
const page = (log: Frame[], after: number, limit: number) => {
  const rows = log.filter((x) => x.seq > after);
  return { events: rows.slice(0, limit), hasMore: rows.length > limit };
};

describe("convergence to the log", () => {
  function backfill(r: FrameReducer, log: Frame[], now: number): Frame[] {
    const out: Frame[] = [];
    for (;;) {
      const p = page(log, r.cursor, 7);
      p.events.forEach((e) => out.push(...r.push(e, now)));
      if (!p.hasMore) return out;
    }
  }

  it("a truncated replay is repaired by paging from the cursor", () => {
    const { run, truth } = seedLog(60);
    const r = new FrameReducer();
    const got: Frame[] = [];
    run.slice(-5).forEach((x) => got.push(...r.push(x, 0))); // the socket sent the newest five, then the marker
    expect(got).toEqual([]);
    expect(r.pending()).toBe(5);
    got.push(...backfill(r, run, 0));
    expect(seqsOf(got)).toEqual(truth);
    expect(r.pending()).toBe(0);
  });

  it.each(Array.from({ length: 30 }, (_, i) => i))("loss, duplication and reordering converge (seed %i)", (seed) => {
    const rand = rng(seed + 1);
    const { run, truth } = seedLog(70);
    const wire: Frame[] = [];
    for (const x of run) {
      if (rand() < 0.15) continue; // lost
      wire.push(x);
      if (rand() < 0.2) wire.push(x); // twice
    }
    for (let i = 0; i < wire.length - 1; i++) if (rand() < 0.2) [wire[i], wire[i + 1]] = [wire[i + 1], wire[i]];
    const r = new FrameReducer(0, 8, 3000);
    const delivered: Frame[] = [];
    let now = 0;
    for (const x of wire) {
      now += 1000;
      delivered.push(...r.push(x, now));
      if (r.gap(now)) delivered.push(...backfill(r, run, now));
    }
    delivered.push(...backfill(r, run, now)); // the periodic safety poll
    expect(seqsOf(delivered)).toEqual(truth);
    expect(new Set(seqsOf(delivered)).size).toBe(truth.length);
    expect(r.cursor).toBe(truth[truth.length - 1]);
    expect(r.pending()).toBe(0);
  });
});

describe("parseFrame", () => {
  it("reads event, truncated, ready and error frames", () => {
    const ev = parseFrame(JSON.stringify({ channel: "collab", seq: 4, prevSeq: null, kind: "leader.assigned", actor: "a", payload: { x: 1 }, ts: "t", schemaVersion: 1, runId: "r1" }));
    expect(ev).toMatchObject({ type: "event", frame: { seq: 4, prevSeq: null, kind: "leader.assigned", actor: "a" } });
    expect(parseFrame({ replayTruncated: true, oldestSeqSent: 9, afterSeq: 2 })).toEqual({ type: "truncated", oldestSeqSent: 9, afterSeq: 2 });
    expect(parseFrame({ control: "ready", afterSeq: 2, headSeq: 8, cursorAhead: true, schemaVersion: 1 })).toEqual({ type: "ready", afterSeq: 2, headSeq: 8, cursorAhead: true, schemaVersion: 1 });
    expect(parseFrame({ error: { code: "RATE_LIMITED", message: "x" }, retryAfterSeconds: 12 })).toEqual({ type: "error", code: "RATE_LIMITED", retryAfterSeconds: 12 });
  });
  it("ignores unknown control values, junk and unusable events without throwing", () => {
    expect(parseFrame({ control: "something-new" })).toEqual({ type: "unknown" });
    expect(parseFrame("not json")).toEqual({ type: "unknown" });
    expect(parseFrame(null)).toEqual({ type: "unknown" });
    expect(parseFrame({ seq: "7", kind: "x" })).toEqual({ type: "unknown" });
    expect(parseFrame({ seq: -1, kind: "x" })).toEqual({ type: "unknown" });
  });
  it("an unknown kind and unknown fields are still applied; a non-object payload becomes {}", () => {
    const p = parseFrame({ seq: 3, kind: "future.thing", payload: "x", brandNew: 1 });
    expect(p).toMatchObject({ type: "event", frame: { seq: 3, kind: "future.thing", payload: {}, prevSeq: null, schemaVersion: 1 } });
  });
});

describe("normalizeHistory", () => {
  it("reads the enriched page", () => {
    const p = normalizeHistory({ events: [{ seq: 5, prevSeq: 3, actor: "a", kind: "k", payload: {}, ts: "t" }], hasMore: true, nextCursor: 5, headSeq: 20 }, 200);
    expect(p).toMatchObject({ hasMore: true, nextCursor: 5, headSeq: 20 });
    expect(p.events[0].prevSeq).toBe(3);
  });
  it("older shape: no hasMore means more may exist exactly when the page is full; no prevSeq", () => {
    const rows = [1, 2, 3].map((seq) => ({ seq, actor: "a", kind: "k", payload: {}, ts: "t" }));
    expect(normalizeHistory({ events: rows }, 3).hasMore).toBe(true);
    expect(normalizeHistory({ events: rows }, 4).hasMore).toBe(false);
    expect(normalizeHistory({ events: rows }, 4).events[0].prevSeq).toBeNull();
  });
  it("junk rows and bodies are dropped", () => {
    expect(normalizeHistory({ events: [null, 4, { seq: "x" }] }, 10).events).toEqual([]);
    expect(normalizeHistory(null, 10).events).toEqual([]);
  });
});

describe("backoff, polling interval and close codes", () => {
  it("nominal schedule 1,2,4,8,16,30,30 with equal jitter d/2 + random(0, d/2)", () => {
    const nominal = [0, 1, 2, 3, 4, 5, 6].map((n) => reconnectDelayMs(n, () => 1));
    expect(nominal).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000]);
    expect([0, 3, 9].map((n) => reconnectDelayMs(n, () => 0))).toEqual([500, 4000, 15000]);
  });
  it("a close code's floor wins over a shorter delay", () => {
    expect(reconnectDelayMs(0, () => 0, 5000)).toBe(5000);
    expect(reconnectDelayMs(5, () => 1, 5000)).toBe(30000);
  });
  it("3 s +-20% not live, 20 s live, 30 s hidden", () => {
    expect(pollIntervalMs(false, false, () => 0)).toBe(2400);
    expect(pollIntervalMs(false, false, () => 1)).toBe(3600);
    expect(pollIntervalMs(true, false)).toBe(20000);
    expect(pollIntervalMs(false, true)).toBe(30000);
    expect(pollIntervalMs(true, true)).toBe(30000);
  });
  it.each([
    [1006, { action: "retry", floorMs: 0 }], [1000, { action: "retry", floorMs: 0 }], [1013, { action: "retry", floorMs: 5000 }],
    [4408, { action: "retry", floorMs: 5000, strike: "slow" }], [4401, { action: "retry", floorMs: 0, strike: "auth" }],
    [4403, { action: "retry", floorMs: 0, strike: "auth" }], [4404, { action: "stop", status: "access_lost" }],
    [4410, { action: "stop", status: "access_lost" }],
  ])("close %i", (code, want) => expect(closePolicy(code)).toEqual(want));
  it("4429 waits retryAfterSeconds, 30 s when absent", () => {
    expect(closePolicy(4429, 12)).toEqual({ action: "wait", floorMs: 12000 });
    expect(closePolicy(4429)).toEqual({ action: "wait", floorMs: 30000 });
  });
});

describe("status text and announcements", () => {
  it("every status has words and update_required is appended, not a colour", () => {
    expect(statusText("live", false)).toBe("Live");
    expect(statusText("reconnecting", false)).toMatch(/Reconnecting/);
    expect(statusText("polling", false)).toMatch(/paused/);
    expect(statusText("live", true)).toMatch(/Live\. This page is out of date, reload/);
  });
  it("announces reconnecting only after 5 s, once, then 'back' once; never each retry", () => {
    vi.useFakeTimers();
    const say = vi.fn();
    const a = createStatusAnnouncer(say);
    a.onStatus("live");
    a.onStatus("reconnecting");
    vi.advanceTimersByTime(4000);
    expect(say).not.toHaveBeenCalled();
    a.onStatus("polling");
    a.onStatus("reconnecting"); // retries flip the state; still no second announcement
    vi.advanceTimersByTime(1500);
    expect(say).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(60_000);
    expect(say).toHaveBeenCalledTimes(1);
    a.onStatus("live");
    expect(say).toHaveBeenCalledTimes(2);
    expect(say.mock.calls[1][0]).toMatch(/back/);
    a.onStatus("live");
    expect(say).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });
  it("a blip shorter than 5 s says nothing at all; access_lost is urgent", () => {
    vi.useFakeTimers();
    const say = vi.fn();
    const a = createStatusAnnouncer(say);
    a.onStatus("reconnecting");
    vi.advanceTimersByTime(2000);
    a.onStatus("live");
    vi.advanceTimersByTime(10_000);
    expect(say).not.toHaveBeenCalled();
    a.onStatus("access_lost");
    expect(say).toHaveBeenCalledWith(expect.stringMatching(/no longer have access/), true);
    vi.useRealTimers();
  });
});

describe("stale state and rate limits on mutations", () => {
  it.each(["STALE_CLIENT_STATE", "STALE_INTENT_VERSION", "INTENT_CONFLICT", "DIGEST_MISMATCH", "STALE_COMMENT_VERSION", "VERSION_CONFLICT"])("%s on 409 is stale", (code) => {
    expect(isStale(new ApiError("x", code, 409))).toBe(true);
    expect(mutationError(new ApiError("raw", code, 409), () => "fallback")).toMatch(/changed while you were looking/);
  });
  it("other 409s and non-API errors are not stale", () => {
    expect(isStale(new ApiError("x", "NOT_PENDING", 409))).toBe(false);
    expect(isStale(new Error("x"))).toBe(false);
    expect(mutationError(new ApiError("raw", "NOT_PENDING", 409), (e) => `fallback:${(e as Error).message}`)).toBe("fallback:raw");
  });
  it("429 reads Retry-After when the client kept it, 30 s otherwise, capped", () => {
    const e = Object.assign(new ApiError("slow", "RATE_LIMITED", 429), { retryAfter: 12 });
    expect(cooldownSeconds(e)).toBe(12);
    expect(cooldownSeconds(new ApiError("slow", "RATE_LIMITED", 429))).toBe(30);
    expect(cooldownSeconds(Object.assign(new ApiError("s", "RATE_LIMITED", 429), { retryAfter: 9999 }))).toBe(300);
    expect(cooldownSeconds(new ApiError("s", "X", 500))).toBeNull();
    expect(mutationError(e, () => "x")).toMatch(/Wait 12 seconds/);
  });
});

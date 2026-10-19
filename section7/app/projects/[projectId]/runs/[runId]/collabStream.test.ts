// Part 46: the stream controller driven with a fake socket, fake history and fake timers (node env).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { type SocketLike, type StreamDeps, createCollabStream } from "./collabStream";
import type { ConnStatus, Frame } from "./realtimeCore";

class FakeSocket implements SocketLike {
  static all: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((e: { code?: number }) => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeSocket.all.push(this); }
  close() { this.closed = true; }
  open() { this.onopen?.(); }
  send(o: unknown) { this.onmessage?.({ data: JSON.stringify(o) }); }
  drop(code = 1006) { this.onclose?.({ code }); }
}
const last = () => FakeSocket.all[FakeSocket.all.length - 1];
const ev = (seq: number, prevSeq: number | null, extra: Record<string, unknown> = {}) =>
  ({ channel: "collab", seq, prevSeq, actor: "u", kind: "participant.joined", payload: {}, ts: "t", schemaVersion: 1, ...extra });
const ready = (extra: Record<string, unknown> = {}) => ({ channel: "collab", control: "ready", afterSeq: 0, headSeq: 0, cursorAhead: false, schemaVersion: 1, ...extra });
const tick = async (ms = 0) => { await vi.advanceTimersByTimeAsync(ms); };

function setup(over: Partial<StreamDeps> = {}) {
  const delivered: number[] = [];
  const statuses: ConnStatus[] = [];
  const resets = vi.fn();
  let updateRequired = false;
  const mint = vi.fn(async () => ({ ticket: "tk", fingerprint: "fp" }));
  const history = vi.fn(async (_after: number, _limit: number): Promise<unknown> => ({ events: [] }));
  const s = createCollabStream(
    { mintTicket: mint, openSocket: (u) => new FakeSocket(u), fetchHistory: history, socketBase: "ws://x", socketPath: "/c", random: () => 1, ...over },
    {
      onEvents: (fs: Frame[]) => delivered.push(...fs.map((x) => x.seq)),
      onReset: resets,
      onState: (st) => { statuses.push(st.status); updateRequired = st.updateRequired; },
    },
  );
  return { s, delivered, statuses, resets, mint, history, updateRequired: () => updateRequired };
}

beforeEach(() => { vi.useFakeTimers(); FakeSocket.all = []; });
afterEach(() => vi.useRealTimers());

describe("connecting and going live", () => {
  it("mints a ticket, sends cursor, schema version and control=1; ready makes it live", async () => {
    const t = setup();
    t.s.start();
    await tick();
    const u = new URL(last().url.replace("ws://", "http://"));
    expect(u.pathname).toBe("/c");
    expect(u.searchParams.get("ticket")).toBe("tk");
    expect(u.searchParams.get("afterSeq")).toBe("0");
    expect(u.searchParams.get("schemaVersion")).toBe("1");
    expect(u.searchParams.get("control")).toBe("1");
    expect(u.searchParams.get("fingerprint")).toBe("fp");
    last().open();
    expect(t.s.snapshot().status).toBe("catching_up");
    last().send(ev(1, null));
    last().send(ready());
    expect(t.s.snapshot().status).toBe("live");
    expect(t.delivered).toEqual([1]);
    t.s.stop();
  });

  it("against a server without ready, live comes after one quiet second", async () => {
    const t = setup();
    t.s.start();
    await tick();
    last().open();
    await tick(900);
    last().send(ev(1, null));
    await tick(900);
    expect(t.s.snapshot().status).toBe("catching_up");
    await tick(200);
    expect(t.s.snapshot().status).toBe("live");
    t.s.stop();
  });

  it("without a WebSocket it polls only and says so", async () => {
    const t = setup({ hasSocket: false });
    t.history.mockResolvedValueOnce({ events: [{ seq: 2, actor: "a", kind: "k", payload: {}, ts: "t" }] });
    t.s.start();
    await tick();
    expect(t.s.snapshot().status).toBe("polling");
    expect(FakeSocket.all).toHaveLength(0);
    expect(t.delivered).toEqual([2]);
    t.s.stop();
  });
});

describe("ordering, duplicates, gaps", () => {
  it("delivers in order once, holds out-of-order frames, drops repeats", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().send(ready());
    last().send(ev(1, null));
    last().send(ev(3, 2)); // waits for 2
    last().send(ev(1, null)); // repeat
    expect(t.delivered).toEqual([1]);
    last().send(ev(2, 1));
    expect(t.delivered).toEqual([1, 2, 3]);
    expect(t.s.snapshot().duplicates).toBe(1);
    expect(t.s.snapshot().pending).toBe(0);
    t.s.stop();
  });

  it("a frame that stays held for 3 s triggers a backfill from the cursor, which releases it", async () => {
    const t = setup();
    t.history.mockImplementation(async (after: number) => ({
      events: after < 2 ? [{ seq: 2, prevSeq: 1, actor: "u", kind: "k", payload: {}, ts: "t" }] : [], hasMore: false,
    }));
    t.s.start(); await tick(); last().open(); last().send(ready());
    last().send(ev(1, null));
    last().send(ev(3, 2));
    expect(t.delivered).toEqual([1]);
    await tick(2900);
    expect(t.history).not.toHaveBeenCalled();
    await tick(200);
    expect(t.history).toHaveBeenCalledWith(1, 200);
    expect(t.delivered).toEqual([1, 2, 3]);
    t.s.stop();
  });

  it("replayTruncated pages the rest over history, looping while hasMore", async () => {
    const t = setup();
    const row = (seq: number, prevSeq: number | null) => ({ seq, prevSeq, actor: "u", kind: "k", payload: {}, ts: "t" });
    const pages: Record<number, unknown> = {
      0: { events: [row(1, null), row(2, 1)], hasMore: true },
      2: { events: [row(3, 2), row(4, 3)], hasMore: false },
    };
    t.history.mockImplementation(async (after: number) => pages[after] ?? { events: [] });
    t.s.start(); await tick(); last().open();
    last().send(ev(5, 4)); // newest frame, predecessors never sent
    last().send({ channel: "collab", replayTruncated: true, oldestSeqSent: 5, afterSeq: 0 });
    await tick();
    expect(t.history.mock.calls.map((c) => c[0])).toEqual([0, 2]);
    expect(t.delivered).toEqual([1, 2, 3, 4, 5]);
    t.s.stop();
  });

  it("ready.cursorAhead resets cursor, clears the list and reloads history from 0", async () => {
    const t = setup();
    t.history.mockResolvedValue({ events: [{ seq: 1, actor: "u", kind: "k", payload: {}, ts: "t" }], hasMore: false });
    t.s.start(); await tick(); last().open();
    last().send(ev(9, null));
    expect(t.s.snapshot().cursor).toBe(9);
    last().send(ready({ cursorAhead: true, headSeq: 1 }));
    await tick();
    expect(t.resets).toHaveBeenCalledTimes(1);
    expect(t.history).toHaveBeenCalledWith(0, 200);
    expect(t.s.snapshot().cursor).toBe(1);
    t.s.stop();
  });

  it("junk and unknown control frames never throw and never move the cursor", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open();
    last().onmessage!({ data: "{{{" });
    last().send({ control: "brand-new" });
    last().send({ seq: "x" });
    expect(t.s.snapshot().cursor).toBe(0);
    t.s.stop();
  });
});

describe("reconnect, backoff, polling fallback", () => {
  it("a drop shows reconnecting, polls at once, reconnects with backoff using the current cursor", async () => {
    const t = setup();
    t.history.mockResolvedValue({ events: [{ seq: 5, actor: "u", kind: "k", payload: {}, ts: "t" }], hasMore: false });
    t.s.start(); await tick(); last().open(); last().send(ready()); last().send(ev(1, null));
    last().drop(1006);
    expect(t.s.snapshot().status).toBe("reconnecting");
    await tick(); // first poll runs immediately
    expect(t.history).toHaveBeenCalledTimes(1);
    expect(t.delivered).toEqual([1, 5]);
    expect(FakeSocket.all).toHaveLength(1);
    await tick(1000); // random()=1: attempt 0 waits 1 s
    expect(FakeSocket.all).toHaveLength(2);
    expect(new URL(last().url.replace("ws://", "http://")).searchParams.get("afterSeq")).toBe("5");
    expect(t.mint).toHaveBeenCalledTimes(2); // a fresh ticket per attempt
    t.s.stop();
  });

  it("repeated failures back off 1,2,4 s and keep polling every ~3 s", async () => {
    const t = setup();
    t.mint.mockRejectedValue(new ApiError("down", "UNAVAILABLE", 503));
    t.s.start();
    await tick();
    expect(t.s.snapshot().status).toBe("reconnecting");
    await tick(1000); expect(t.mint).toHaveBeenCalledTimes(2);
    await tick(2000); expect(t.mint).toHaveBeenCalledTimes(3);
    await tick(4000); expect(t.mint).toHaveBeenCalledTimes(4);
    await tick(1000); expect(t.mint).toHaveBeenCalledTimes(4); // next wait is 8 s, not sooner
    const polls = t.history.mock.calls.length;
    expect(polls).toBeGreaterThanOrEqual(3);
    t.s.stop();
  });

  it("keeps the backoff until a socket stayed live for 30 s, then resets it", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().send(ready());
    last().drop(); await tick(1000); // attempt 0 -> 1 s
    last().open(); last().send(ready());
    last().drop(); // dropped before 30 s live: attempt 1 -> 2 s
    await tick(1900); expect(FakeSocket.all).toHaveLength(2);
    await tick(100); expect(FakeSocket.all).toHaveLength(3);
    last().open(); last().send(ready());
    await tick(31_000); // stable: backoff resets
    last().drop();
    await tick(1000);
    expect(FakeSocket.all).toHaveLength(4);
    t.s.stop();
  });

  it("while live only the 20 s safety poll runs; not live it is ~3 s", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().send(ready());
    await tick(19_000); expect(t.history).not.toHaveBeenCalled();
    await tick(2000); expect(t.history).toHaveBeenCalledTimes(1);
    t.s.stop();
  });

  it("1013 and 4408 wait at least 5 s; three 4408 in 5 minutes stay polling for 5 minutes", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().send(ready());
    last().drop(1013);
    await tick(4900); expect(FakeSocket.all).toHaveLength(1);
    await tick(200); expect(FakeSocket.all).toHaveLength(2);
    last().open(); last().drop(4408);
    await tick(6000); expect(FakeSocket.all).toHaveLength(3);
    last().open(); last().drop(4408);
    await tick(6000); expect(FakeSocket.all).toHaveLength(4);
    last().open(); last().drop(4408); // third in 5 minutes
    expect(t.s.snapshot().status).toBe("polling");
    await tick(60_000); expect(FakeSocket.all).toHaveLength(4);
    await tick(250_000); expect(FakeSocket.all.length).toBeGreaterThanOrEqual(5);
    t.s.stop();
  });

  it("4429 waits the server's retryAfterSeconds (polling meanwhile)", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().send(ready());
    last().send({ channel: "collab", error: { code: "RATE_LIMITED", message: "x" }, retryAfterSeconds: 20 });
    last().drop(4429);
    expect(t.s.snapshot().status).toBe("polling");
    await tick(19_000); expect(FakeSocket.all).toHaveLength(1);
    await tick(1500); expect(FakeSocket.all).toHaveLength(2);
    t.s.stop();
  });

  it("4401 mints once more; a second auth close in a row stops looping and polls", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().drop(4401);
    await tick(1000); expect(FakeSocket.all).toHaveLength(2);
    last().open(); last().drop(4403);
    expect(t.s.snapshot().status).toBe("polling");
    await tick(100_000); expect(FakeSocket.all).toHaveLength(2);
    t.s.stop();
  });
});

describe("access, offline, visibility, schema", () => {
  it.each([4404, 4410])("close %i stops sockets and polling and says access was lost", async (code) => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().send(ready());
    last().drop(code);
    expect(t.s.snapshot().status).toBe("access_lost");
    await tick(120_000);
    expect(FakeSocket.all).toHaveLength(1);
    expect(t.history).not.toHaveBeenCalled();
  });

  it.each([401, 404])("a ticket mint that answers %i is access lost, no retry loop", async (status) => {
    const t = setup();
    t.mint.mockRejectedValue(new ApiError("no", "NOT_FOUND", status));
    t.s.start(); await tick(100_000);
    expect(t.s.snapshot().status).toBe("access_lost");
    expect(t.mint).toHaveBeenCalledTimes(1);
  });

  it("history 404 or 403 while polling is access lost", async () => {
    const t = setup();
    t.history.mockRejectedValue(new ApiError("gone", "NOT_FOUND", 404));
    t.s.start(); await tick(); last().open(); last().drop();
    await tick();
    expect(t.s.snapshot().status).toBe("access_lost");
  });

  it("history 429 is not retried before Retry-After", async () => {
    const t = setup();
    t.history.mockRejectedValueOnce(Object.assign(new ApiError("slow", "RATE_LIMITED", 429), { retryAfter: 40 }));
    t.history.mockResolvedValue({ events: [], hasMore: false });
    t.s.start(); await tick(); last().open(); last().drop();
    await tick();
    expect(t.history).toHaveBeenCalledTimes(1);
    await tick(30_000);
    expect(t.history).toHaveBeenCalledTimes(1);
    await tick(15_000);
    expect(t.history.mock.calls.length).toBeGreaterThan(1);
    t.s.stop();
  });

  it("offline closes the socket and does not retry; online reconnects at once", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().send(ready());
    const first = last();
    t.s.setOnline(false);
    expect(first.closed).toBe(true);
    expect(t.s.snapshot().status).toBe("offline");
    await tick(120_000);
    expect(FakeSocket.all).toHaveLength(1);
    expect(t.history).not.toHaveBeenCalled();
    t.s.setOnline(true);
    await tick();
    expect(FakeSocket.all).toHaveLength(2);
    t.s.stop();
  });

  it("a hidden tab stretches polling to 30 s and a visible tab polls at once", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().drop();
    await tick(); // immediate poll
    t.s.setHidden(true);
    t.history.mockClear();
    await tick(10_000);
    await tick(20_001);
    expect(t.history.mock.calls.length).toBeLessThanOrEqual(2);
    t.history.mockClear();
    t.s.setHidden(false);
    await tick();
    expect(t.history).toHaveBeenCalled();
    t.s.stop();
  });

  it("a frame with a newer schemaVersion keeps its place, advances the cursor and flags update required", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().send(ready());
    last().send(ev(1, null, { schemaVersion: 2, payload: { omitted: true, reason: "schema_unsupported" } }));
    last().send(ev(2, 1));
    expect(t.delivered).toEqual([1, 2]);
    expect(t.updateRequired()).toBe(true);
    t.s.stop();
  });

  it("an old callback after a reconnect cannot touch the new socket's state", async () => {
    const t = setup();
    t.s.start(); await tick(); last().open(); last().send(ready());
    const old = last();
    old.drop(); await tick(1000);
    last().open(); last().send(ready());
    old.drop(4410); // stale socket closing late
    expect(t.s.snapshot().status).toBe("live");
    t.s.stop();
  });
});

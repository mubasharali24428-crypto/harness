import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EventType } from "@contracts/events";
import type { EventEnvelope } from "@contracts/events";
import {
  EventStreamController,
  mergeEvents,
  type SocketLike,
} from "@/lib/useEventStream";
import {
  createRefetchCoalescer,
  REFETCH_COALESCE_MS,
  type RefetchCoalescer,
} from "@/lib/backoff";

function evt(
  seq: number,
  type: EventType = EventType.EXECUTION_STARTED,
  payload: Record<string, unknown> = {},
): EventEnvelope {
  return { type, payload, ts: "2026-01-01T00:00:00Z", seq, projectId: "p1", runId: "r1" };
}

// ---------------------------------------------------------------------------
// Mock WS + deterministic scheduler for lifecycle tests (node env, no DOM).
// NOTE: ticket minting is async (a promise), so a freshly started controller
// does not have its first socket until the microtask queue flushes. Tests use
// `await flush()` (or vi.waitFor) before touching MockSocket.instances.
// ---------------------------------------------------------------------------

class MockSocket implements SocketLike {
  static instances: MockSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((evt: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closedByTest = false;

  constructor(public url: string) {
    MockSocket.instances.push(this);
  }

  close(): void {
    this.closedByTest = true;
  }

  open(): void {
    this.onopen?.();
  }

  message(data: unknown): void {
    this.onmessage?.({ data });
  }

  error(): void {
    this.onerror?.();
  }

  closeFromServer(): void {
    const handler = this.onclose;
    // A real server-side close does not run the client's close() first.
    this.onclose = null;
    handler?.();
  }

  lastUrl(): string {
    return this.url;
  }
}

interface ScheduledCall {
  fn: () => void;
  delayMs: number;
  cancelled: boolean;
}

/** Deterministic in-test timer queue; replaces setTimeout entirely. */
function makeScheduler() {
  const calls: ScheduledCall[] = [];
  const schedule = (fn: () => void, delayMs: number) => {
    const call: ScheduledCall = { fn, delayMs, cancelled: false };
    calls.push(call);
    return () => {
      call.cancelled = true;
    };
  };
  const fireNext = (): ScheduledCall | undefined => {
    const next = calls.find((c) => !c.cancelled);
    if (!next) return undefined;
    next.cancelled = true;
    next.fn();
    return next;
  };
  const pending = () => calls.filter((c) => !c.cancelled);
  return { calls, schedule, fireNext, pending };
}

const TICKET = vi.fn(async (): Promise<string> => "test-ticket");

interface Harness {
  controller: EventStreamController;
  scheduler: ReturnType<typeof makeScheduler>;
  states: Array<{ state: string; attempt: number; nextDelayMs: number }>;
  events: EventEnvelope[];
}

async function makeController(): Promise<Harness> {
  MockSocket.instances = [];
  const scheduler = makeScheduler();
  const states: Harness["states"] = [];
  const events: EventEnvelope[] = [];
  const controller = new EventStreamController({
    mintTicket: TICKET,
    socketFactory: (url) => new MockSocket(url),
    schedule: scheduler.schedule,
    onEvent: (e) => events.push(e),
    onStateChange: (s) => states.push({ ...s }),
  });
  controller.start("proj-1", "tok-1");
  await flush(); // ticket mint is async — wait for socket #0 to exist
  return { controller, scheduler, states, events };
}

/** Drain the microtask queue so pending promise callbacks run. */
async function flush(times = 3): Promise<void> {
  for (let i = 0; i < times; i++) await Promise.resolve();
}

beforeEach(() => {
  TICKET.mockClear();
  TICKET.mockImplementation(async () => "test-ticket");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("mergeEvents (existing behavior — unchanged)", () => {
  it("appends, dedupes and sorts by seq", () => {
    expect(mergeEvents([], evt(1))).toHaveLength(1);
    expect(mergeEvents([evt(1), evt(2)], evt(2))).toHaveLength(2);
    expect(mergeEvents([evt(3)], evt(1)).map((e) => e.seq)).toEqual([1, 3]);
  });
});

describe("H-012 pill state machine on mock WS lifecycle", () => {
  it("connecting → live when the socket opens", async () => {
    const h = await makeController();
    expect(h.controller.getStatus().state).toBe("connecting");
    MockSocket.instances[0].open();
    expect(h.controller.getStatus().state).toBe("open");
    expect(h.controller.getStatus().attempt).toBe(0);
  });

  it("live → reconnecting(attempt=1) after a drop; backoff timer scheduled", async () => {
    const h = await makeController();
    const sock0 = MockSocket.instances[0];
    sock0.open();
    sock0.closeFromServer();

    let s = h.controller.getStatus();
    expect(s.state).toBe("reconnecting");
    expect(s.attempt).toBe(1);
    expect(s.nextDelayMs).toBe(1000); // ceiling of attempt 1
    expect(h.scheduler.pending()).toHaveLength(1);
    expect(h.scheduler.pending()[0].delayMs).toBeLessThanOrEqual(1000);

    // firing it mints a fresh single-use ticket (H8) and opens socket #2
    const minted = TICKET.mock.calls.length;
    h.scheduler.fireNext();
    await flush();
    expect(TICKET.mock.calls.length).toBe(minted + 1);
    expect(MockSocket.instances).toHaveLength(2);
    s = h.controller.getStatus();
    expect(s.state).toBe("reconnecting"); // still not open
    expect(s.attempt).toBe(1); // unchanged — no NEW failure was counted

    // recover then drop again: successful open resets the attempt counter
    MockSocket.instances[1].open();
    MockSocket.instances[1].closeFromServer();
    s = h.controller.getStatus();
    expect(s.state).toBe("reconnecting");
    expect(s.attempt).toBe(1); // reset by the successful open
    expect(s.nextDelayMs).toBe(1000);
  });

  it("attempt count escalates across consecutive failures and resets after recovery", async () => {
    const h = await makeController();
    const sock0 = MockSocket.instances[0];
    sock0.open();

    // fail #1 → attempt 1
    sock0.closeFromServer();
    expect(h.controller.getStatus().attempt).toBe(1);
    h.scheduler.fireNext();
    await flush();

    // fail #2 → attempt 2 (socket #1 never opened)
    MockSocket.instances[1].closeFromServer();
    let s = h.controller.getStatus();
    expect(s.state).toBe("reconnecting");
    expect(s.attempt).toBe(2);
    expect(s.nextDelayMs).toBe(2000);
    expect(h.scheduler.pending()[0].delayMs).toBeLessThanOrEqual(2000);

    // fail #3 → attempt 3, ceiling 4000
    h.scheduler.fireNext();
    await flush();
    MockSocket.instances[2].closeFromServer();
    s = h.controller.getStatus();
    expect(s.attempt).toBe(3);
    expect(s.nextDelayMs).toBe(4000);

    // recover: open clears attempt to 0 and pill returns to live
    h.scheduler.fireNext();
    await flush();
    MockSocket.instances[3].open();
    s = h.controller.getStatus();
    expect(s.state).toBe("open");
    expect(s.attempt).toBe(0);
  });

  it("reconnect URL carries afterSeq resume point and encoded fresh ticket", async () => {
    const h = await makeController();
    const sock0 = MockSocket.instances[0];
    sock0.open();
    sock0.message(JSON.stringify(evt(41)));
    sock0.closeFromServer();
    h.scheduler.fireNext();
    await flush();

    const url2 = MockSocket.instances[1].lastUrl();
    expect(url2).toContain("afterSeq=41");
    expect(url2).toContain("ticket=test-ticket");
    expect(url2).toContain("/api/v1/ws/proj-1");
  });

  it("stop() closes the socket and reports offline (closed)", async () => {
    const h = await makeController();
    const sock = MockSocket.instances[0];
    sock.open();
    h.controller.stop();
    expect(sock.closedByTest).toBe(true);
    expect(h.controller.getStatus().state).toBe("closed");
    expect(h.controller.getStatus().nextDelayMs).toBe(0);
  });

  it("ticket failure also schedules a backoff reconnect", async () => {
    TICKET.mockImplementation(async () => {
      throw new Error("401");
    });
    const scheduler = makeScheduler();
    const states: Harness["states"] = [];
    const controller = new EventStreamController({
      mintTicket: TICKET,
      socketFactory: (url) => new MockSocket(url),
      schedule: scheduler.schedule,
      onStateChange: (s) => states.push({ ...s }),
    });
    controller.start("proj-1", "tok-1");
    await flush();
    const s = controller.getStatus();
    expect(s.state).toBe("reconnecting");
    expect(s.attempt).toBe(1);
    expect(scheduler.pending()).toHaveLength(1);
  });
});

describe("H-012 guarded JSON.parse (malformed-frame resilience)", () => {
  it("drops malformed frames without throwing and keeps the stream alive", async () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const h = await makeController();
    const sock = MockSocket.instances[0];
    sock.open();

    expect(() => sock.message("{not json at all")).not.toThrow();
    expect(() => sock.message(undefined)).not.toThrow();
    expect(() => sock.message(null)).not.toThrow();
    expect(() => sock.message(42)).not.toThrow();
    expect(() => sock.message("{}")).not.toThrow(); // object but no numeric seq
    expect(() => sock.message('{"seq":"9"}')).not.toThrow(); // non-numeric seq

    expect(h.events).toHaveLength(0);
    expect(consoleSpy.mock.calls.length).toBeGreaterThanOrEqual(4); // logged, never silent

    // stream still healthy afterwards
    sock.message(JSON.stringify(evt(7)));
    expect(h.events).toHaveLength(1);
    expect(h.controller.getLastSeq()).toBe(7);
    consoleSpy.mockRestore();
  });

  it("a throwing consumer cannot tear down the socket either", async () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    MockSocket.instances = []; // isolate from prior tests' sockets
    const scheduler = makeScheduler();
    const kept: EventEnvelope[] = [];
    const controller = new EventStreamController({
      mintTicket: TICKET,
      socketFactory: (url) => new MockSocket(url),
      schedule: scheduler.schedule,
      onEvent: (e) => {
        if (e.seq === 5) throw new Error("consumer bug");
        kept.push(e);
      },
    });
    controller.start("proj-1", "tok-1");
    await flush();
    const sock = MockSocket.instances[0];
    sock.open();
    expect(() => sock.message(JSON.stringify(evt(5)))).not.toThrow();
    sock.message(JSON.stringify(evt(6)));
    expect(kept.map((e) => e.seq)).toEqual([6]);
    expect(controller.getStatus().state).toBe("open"); // socket survived
    consoleSpy.mockRestore();
  });

  it("stale-generation messages are ignored", async () => {
    const h = await makeController();
    const sock0 = MockSocket.instances[0];
    sock0.open();
    sock0.closeFromServer(); // generation bumped by reconnect scheduling
    h.scheduler.fireNext();
    await flush();
    sock0.message(JSON.stringify(evt(99))); // old socket delivering late
    expect(h.events).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// H-012: hook-level refetch coalescing — mirrors WorkingArea's wiring with a
// fake event source so the debounce behavior is asserted end-to-end.
// ---------------------------------------------------------------------------

describe("H-012 refetch coalescing (WorkingArea wiring shape)", () => {
  class FakeBus {
    listeners: Array<() => void> = [];
    length = 0;
    push(n: number) {
      this.length += n;
      this.listeners.forEach((l) => l());
    }
  }

  function mount(bus: FakeBus): { fetches: number[]; coalescer: RefetchCoalescer } {
    const fetches: number[] = [];
    const refresh = vi.fn(() => fetches.push(bus.length));
    const coalescer: RefetchCoalescer = createRefetchCoalescer(REFETCH_COALESCE_MS, () =>
      refresh(),
    );
    bus.listeners.push(() => {
      if (bus.length > 0) coalescer.push();
    });
    return { fetches, coalescer };
  }

  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("a 10-event burst triggers exactly ONE refetch per window", () => {
    const bus = new FakeBus();
    const h = mount(bus);
    for (let i = 0; i < 10; i++) bus.push(1);
    vi.advanceTimersByTime(REFETCH_COALESCE_MS);
    expect(h.fetches.length).toBe(1);
  });

  it("steady trickles still converge to one fetch per 2s window", () => {
    const bus = new FakeBus();
    const h = mount(bus);
    for (let w = 0; w < 3; w++) {
      for (let i = 0; i < 5; i++) {
        bus.push(1);
        vi.advanceTimersByTime(300);
      }
      vi.advanceTimersByTime(2000);
    }
    expect(h.fetches.length).toBe(3);
  });
});

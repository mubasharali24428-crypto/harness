import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BACKOFF_BASE_DELAY_MS,
  BACKOFF_MAX_DELAY_MS,
  REFETCH_COALESCE_MS,
  backoffDelay,
  createRefetchCoalescer,
  exponentialDelay,
} from "@/lib/backoff";

describe("exponentialDelay (H-012 sequence assertion)", () => {
  it("doubles from the 1s base: 1s, 2s, 4s, 8s, 16s", () => {
    expect(BACKOFF_BASE_DELAY_MS).toBe(1000);
    expect([1, 2, 3, 4, 5].map(exponentialDelay)).toEqual([1000, 2000, 4000, 8000, 16000]);
  });

  it("caps at 30s and stays there", () => {
    expect(BACKOFF_MAX_DELAY_MS).toBe(30000);
    expect(exponentialDelay(6)).toBe(30000);
    expect(exponentialDelay(7)).toBe(30000);
    expect(exponentialDelay(20)).toBe(30000);
  });

  it("yields 0 for non-positive attempts", () => {
    expect(exponentialDelay(0)).toBe(0);
    expect(exponentialDelay(-3)).toBe(0);
  });
});

describe("backoffDelay (jitter)", () => {
  it("stays within [0, exponential ceiling] for every attempt", () => {
    for (let attempt = 1; attempt <= 12; attempt++) {
      for (let i = 0; i < 50; i++) {
        const d = backoffDelay(attempt);
        expect(d).toBeGreaterThanOrEqual(0);
        expect(d).toBeLessThanOrEqual(exponentialDelay(attempt));
      }
    }
  });

  it("is deterministic given a stubbed Math.random (full-jitter formula)", () => {
    const randomSpy = vi.spyOn(Math, "random").mockReturnValue(0.25);
    expect(backoffDelay(1)).toBe(250); // 0.25 * 1000
    expect(backoffDelay(3)).toBe(1000); // 0.25 * 4000
    randomSpy.mockRestore();
  });

  it("never exceeds the 30s cap even at large attempts", () => {
    for (let i = 0; i < 100; i++) {
      expect(backoffDelay(50)).toBeLessThanOrEqual(30000);
    }
  });
});

describe("createRefetchCoalescer (H-012 refetch storms)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fires once (trailing) for a burst of pushes inside one window", () => {
    let fires = 0;
    const c = createRefetchCoalescer(REFETCH_COALESCE_MS, () => {
      fires += 1;
    });
    c.push();
    c.push();
    c.push();
    c.push();
    c.push();
    expect(c.pending()).toBe(true);
    vi.advanceTimersByTime(REFETCH_COALESCE_MS);
    expect(fires).toBe(1);
    expect(c.pending()).toBe(false);
  });

  it("resets the window on every push (debounce, not throttle)", () => {
    let fires = 0;
    const c = createRefetchCoalescer(2000, () => {
      fires += 1;
    });
    c.push();
    vi.advanceTimersByTime(1900);
    c.push(); // inside window -> restart full window
    vi.advanceTimersByTime(1900);
    expect(fires).toBe(0);
    expect(c.pending()).toBe(true);
    vi.advanceTimersByTime(100);
    expect(fires).toBe(1);
  });

  it("allows a second fire for activity after the window closed", () => {
    let fires = 0;
    const c = createRefetchCoalescer(2000, () => {
      fires += 1;
    });
    c.push();
    vi.advanceTimersByTime(2000);
    expect(fires).toBe(1);
    c.push();
    vi.advanceTimersByTime(2000);
    expect(fires).toBe(2);
  });

  it("cancel drops a pending fire", () => {
    let fires = 0;
    const c = createRefetchCoalescer(2000, () => {
      fires += 1;
    });
    c.push();
    c.cancel();
    expect(c.pending()).toBe(false);
    vi.advanceTimersByTime(10_000);
    expect(fires).toBe(0);
  });

  it("uses the injected scheduler (no reliance on global timers)", () => {
    let fires = 0;
    const scheduled: Array<{ fn: () => void; delayMs: number; cancelled: boolean }> = [];
    const c = createRefetchCoalescer(
      1234,
      () => {
        fires += 1;
      },
      (fn, delayMs) => {
        const t = { fn, delayMs, cancelled: false };
        scheduled.push(t);
        return () => {
          t.cancelled = true;
        };
      },
    );
    c.push();
    c.push();
    // only one live timer, the earlier one cancelled
    expect(scheduled.filter((t) => !t.cancelled)).toHaveLength(1);
    expect(scheduled[0].cancelled).toBe(true);
    scheduled[scheduled.length - 1].fn();
    expect(fires).toBe(1);
  });
});

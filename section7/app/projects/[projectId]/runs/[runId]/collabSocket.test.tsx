import { afterEach, describe, expect, it, vi } from "vitest";
import { openCollabSocket } from "./collabApi";

class FakeWS {
  static last: FakeWS | null = null;
  onopen: (() => void) | null = null;
  onmessage: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeWS.last = this; }
  close() { this.closed = true; }
}

afterEach(() => { vi.unstubAllGlobals(); FakeWS.last = null; });

describe("collab push socket", () => {
  it("uses a ticket + cursor, wakes the caller per frame and reports liveness", async () => {
    vi.stubGlobal("WebSocket", FakeWS);
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200, text: async () => JSON.stringify({ ticket: "tk1", fingerprint: "fp1" }),
    })));
    const wake = vi.fn();
    const states: boolean[] = [];
    const close = openCollabSocket("p1", "r1", "bearer", () => 7, wake, (l) => states.push(l));
    await vi.waitFor(() => expect(FakeWS.last).not.toBeNull());
    const url = FakeWS.last!.url;
    expect(url).toContain("/api/v1/ws/p1/runs/r1/collab?");
    expect(url).toContain("ticket=tk1");
    expect(url).toContain("afterSeq=7");
    expect(url).toContain("fingerprint=fp1");
    expect(url).not.toContain("bearer");
    FakeWS.last!.onopen!();
    FakeWS.last!.onmessage!();
    expect(wake).toHaveBeenCalledTimes(1);
    expect(states).toEqual([true]);
    close();
    expect(FakeWS.last!.closed).toBe(true);
  });

  it("reports not-live when the ticket cannot be minted (polling stays the fallback)", async () => {
    vi.stubGlobal("WebSocket", FakeWS);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500, text: async () => "{}" })));
    const states: boolean[] = [];
    const close = openCollabSocket("p1", "r1", "t", () => 0, vi.fn(), (l) => states.push(l));
    await vi.waitFor(() => expect(states).toEqual([false]));
    expect(FakeWS.last).toBeNull();
    close();
  });
});

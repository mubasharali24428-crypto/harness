/**
 * WS streaming tests: frame guards, seq dedupe/ordering, upgrade URL law
 * (single-use ?ticket=, never the bearer token; echoed fingerprint on the
 * browser channel ?fingerprint=), reconnect backoff with attempt reset.
 * Mirrors clients/python/tests/test_sdk.py's WS section.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { HarnessHttp } from "../src/runtime.js";
import {
  EventStreamController,
  computeWsFingerprint,
  parseFrame,
  type SocketFactory,
  type SocketLike,
  type TimerScheduler,
} from "../src/ws.js";

function mockHttp(): HarnessHttp {
  return new HarnessHttp({
    fetchImpl: async () =>
      ({
        ok: true,
        status: 200,
        text: async () => "{}",
        headers: new Headers(),
      }) as unknown as Response,
    sleepFn: async () => {},
  });
}

describe("parseFrame (handleMessage extraction)", () => {
  it("accepts a valid envelope", () => {
    const frame = parseFrame('{"seq": 5, "type": "RUN_STARTED"}');
    assert.ok(frame);
    assert.equal(frame.seq, 5);
  });

  it("drops malformed frames instead of throwing", () => {
    assert.equal(parseFrame("not json{"), null);
    assert.equal(parseFrame('["array","frame"]'), null);
    assert.equal(parseFrame('{"type": "no-seq"}'), null);
    assert.equal(parseFrame('{"seq": "five"}'), null);
    assert.equal(parseFrame(42), null);
  });

  it("recognizes the M16-X3 replayTruncated marker", () => {
    const frame = parseFrame('{"replayTruncated": true, "oldestSeqSent": 12}');
    assert.ok(frame);
    assert.ok("replayTruncated" in frame);
  });
});

describe("computeWsFingerprint", () => {
  it("is sha256(ua + ip)[:16] — byte-identical to the server derivation", () => {
    // Reference value computed with: python3 -c
    // "import hashlib; print(hashlib.sha256(b'test-ua127.0.0.1').hexdigest()[:16])"
    assert.equal(computeWsFingerprint("test-ua", "127.0.0.1"), "59c6fb4f28c8d91f");
    // UA-only form (what a fingerprint without IP knowledge degenerates to).
    const uaOnly = computeWsFingerprint("node");
    assert.match(uaOnly, /^[0-9a-f]{16}$/);
    assert.notEqual(computeWsFingerprint("a"), computeWsFingerprint("b"));
  });
});

/** Scripted fake socket: records the URL it was opened with and lets tests
 *  fire lifecycle events + frames. Opens asynchronously like a real socket. */
class FakeSocket implements SocketLike {
  static readonly opened: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((evt: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((evt: { code?: number | null }) => void) | null = null;
  constructor(readonly url: string) {
    FakeSocket.opened.push(this);
    // A real WebSocket fires onopen once the handshake lands — i.e. shortly
    // after the factory returns, never synchronously.
    queueMicrotask(() => this.onopen?.());
  }
  close(): void {
    this.onclose?.({ code: 1000 });
  }
  serverSend(data: unknown): void {
    this.onmessage?.({ data });
  }
}

function factory(): SocketFactory {
  return (url) => new FakeSocket(url);
}

const immediate: (fn: () => void, delayMs: number) => () => void = (fn, _delayMs) => {
  // setTimeout(0), not setImmediate: setImmediate callbacks run before timers
  // in the same turn, which would starve a scheduled reconnect inside a
  // setImmediate-only flush loop.
  const t = setTimeout(fn, 0);
  return () => clearTimeout(t);
};

async function flush(times = 6): Promise<void> {
  for (let i = 0; i < times; i += 1) await new Promise((r) => setTimeout(r, 1));
}

describe("EventStreamController", () => {
  it("mints a ticket per connect and rides ?ticket= (+fingerprint param), never the bearer token", async () => {
    FakeSocket.opened.length = 0;
    const mints: string[] = [];
    const controller = new EventStreamController({
      http: mockHttp(),
      identityBase: "http://localhost:8001",
      mintTicket: async () => {
        mints.push("mint");
        return { ticket: `tkn-${mints.length}`, fingerprint: "ab12cd34ab12cd34" };
      },
      socketFactory: factory(),
      schedule: immediate,
    });
    controller.start("proj1", "session-token");
    await flush();
    assert.ok(FakeSocket.opened[0]);
    const url = FakeSocket.opened[0].url;
    assert.match(url, /^ws:\/\/localhost:8001\/api\/v1\/ws\/proj1\?afterSeq=0/);
    assert.ok(url.includes("ticket=tkn-1"));
    assert.ok(!url.includes("session-token")); // bearer never in URLs
    assert.ok(url.includes(`fingerprint=${encodeURIComponent("ab12cd34ab12cd34")}`));

    // H8: after a drop, EVERY connect mints a FRESH single-use ticket.
    FakeSocket.opened[0].close();
    await flush();
    assert.ok(mints.length >= 2);
    controller.stop();
  });

  it("updates lastSeq from frames and resumes from it on reconnect", async () => {
    FakeSocket.opened.length = 0;
    const received: { seq?: number }[] = [];
    const controller = new EventStreamController({
      http: mockHttp(),
      identityBase: "http://localhost:8001",
      mintTicket: async () => ({ ticket: "t", fingerprint: null }),
      socketFactory: factory(),
      schedule: immediate,
      onEvent: (env) => received.push(env),
    });
    controller.start("proj1", "tok");
    await flush();
    FakeSocket.opened[0].serverSend('{"seq": 7, "type": "plan_proposed"}');
    FakeSocket.opened[0].serverSend('{"seq": 9, "type": "execution_started"}');
    await flush();
    assert.deepEqual(received.map((e) => e.seq), [7, 9]);

    FakeSocket.opened.at(-1)!.close(); // drop → reconnect
    await flush();
    const nextUrl = FakeSocket.opened.at(-1)!.url;
    assert.ok(nextUrl.includes("afterSeq=9")); // resume from highest seq seen
    controller.stop();
  });

  it("dedupes by seq across the reconnect overlap race (mergeEvents semantics)", async () => {
    FakeSocket.opened.length = 0;
    const received: number[] = [];
    const controller = new EventStreamController({
      http: mockHttp(),
      identityBase: "http://localhost:8001",
      mintTicket: async () => ({ ticket: "t", fingerprint: null }),
      socketFactory: factory(),
      schedule: immediate,
      onEvent: (env) => received.push(env.seq),
    });
    controller.start("proj1", "tok");
    await flush();
    const sock = FakeSocket.opened[0];
    sock.serverSend('{"seq": 3}');
    sock.serverSend('{"seq": 3}'); // duplicate dropped
    sock.serverSend('{"seq": 2}'); // stale dropped
    sock.serverSend('{"seq": 5}');
    await flush();
    assert.deepEqual(received, [3, 5]);
    assert.equal(controller.getLastSeq(), 5);

    // Reconnect replays 4 (< lastSeq): still filtered, nothing re-delivered.
    sock.close();
    await flush();
    FakeSocket.opened.at(-1)!.serverSend('{"seq": 4}');
    FakeSocket.opened.at(-1)!.serverSend('{"seq": 6}');
    await flush();
    assert.deepEqual(received, [3, 5, 6]);
    controller.stop();
  });

  it("resets the backoff attempt counter on successful open", async () => {
    FakeSocket.opened.length = 0;
    // Held scheduler: reconnects wait until the test releases them, so the
    // intermediate attempt>0 state is observable.
    const gate: { release: (() => void) | null } = { release: null };
    const heldSchedule: TimerScheduler = (fn, _delayMs) => {
      gate.release = fn;
      return () => {
        gate.release = null;
      };
    };
    const controller = new EventStreamController({
      http: mockHttp(),
      identityBase: "http://localhost:8001",
      mintTicket: async () => ({ ticket: "t", fingerprint: null }),
      socketFactory: factory(),
      schedule: heldSchedule,
    });
    controller.start("proj1", "tok");
    await flush(); // connect #1 opens
    assert.equal(controller.getStatus().state, "open");
    assert.equal(controller.getStatus().attempt, 0);

    FakeSocket.opened[0].close(); // drop #1 → reconnect held in the gate
    await flush();
    assert.equal(controller.getStatus().state, "reconnecting");
    assert.equal(controller.getStatus().attempt, 1);

    gate.release?.(); // reconnect fires; the fresh socket auto-opens
    await flush();
    // A successful open clears the consecutive-failure count — the next drop
    // restarts the backoff sequence from the base delay.
    assert.equal(controller.getStatus().state, "open");
    assert.equal(controller.getStatus().attempt, 0);

    controller.stop();
    assert.equal(controller.getStatus().state, "closed");
  });

  it("surfaces permanent rejections through onPermanentClose and stops", async () => {
    FakeSocket.opened.length = 0;
    let permanent: number | null | undefined;
    const controller = new EventStreamController({
      http: mockHttp(),
      identityBase: "http://localhost:8001",
      mintTicket: async () => ({ ticket: "t", fingerprint: "ff" }),
      socketFactory: factory(),
      schedule: immediate,
      onPermanentClose: (code) => {
        permanent = code;
      },
    });
    controller.start("proj1", "tok");
    await flush();
    FakeSocket.opened[0].onclose?.({ code: 4403 }); // TICKET_FINGERPRINT_MISMATCH
    await flush();
    assert.equal(permanent, 4403);
    assert.equal(controller.getStatus().state, "closed");
  });
});

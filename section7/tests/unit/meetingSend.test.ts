// Mission 12 lane E1 — meeting send flow (H-002 client half).
//
// Contract under test:
// - An @ai send performs EXACTLY ONE network call: POST /messages with the
//   "@ai …" content. S6 replies server-side (real model or honest fallback),
//   so the client's second invokeAi POST is a duplicate and must be GONE.
// - The caller observes thinking state while the reply is in flight
//   (onThinking(true) before the post, onThinking(false) after refresh).

import { afterEach, describe, expect, it, vi } from "vitest";
import { sendWithAi } from "@/lib/meetingSend";
import type { MeetingMsg } from "@/lib/api";

const TOKEN = "tok-123";

function stubFetch(responder: (url: string, init: RequestInit) => unknown) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const body = responder(url, init ?? ({} as RequestInit));
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify(body),
    } as Response;
  });
  vi.stubGlobal("fetch", f);
  return f;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sendWithAi (M12-E1)", () => {
  it("makes exactly ONE call — the /messages post — and no /ai request", async () => {
    const f = stubFetch((url) => {
      if (String(url).includes("/messages?") || String(url).endsWith("/messages")) {
        // M16-X2: the transcript GET answers the pagination envelope now.
        return { items: [{ userId: "u1", content: "@ai status?" }], hasMore: false };
      }
      return { items: [], hasMore: false };
    });
    await sendWithAi({
      meetingId: "mtg-9",
      userId: "u1",
      content: "status?",
      token: TOKEN,
      onMessages: () => {},
      onThinking: () => {},
    });
    const aiCalls = f.mock.calls.filter((c) => String(c[0]).endsWith("/ai"));
    expect(aiCalls).toHaveLength(0);
    // One send + one transcript refresh; the duplicate invokeAi POST is gone.
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("posts the @ai-prefixed content to /messages with bearer auth", async () => {
    const f = stubFetch((_url) => ({ items: [] }));
    await sendWithAi({
      meetingId: "mtg-9",
      userId: "u1",
      content: "status?",
      token: TOKEN,
      onMessages: () => {},
      onThinking: () => {},
    });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v1/meetings/mtg-9/messages");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body));
    expect(body.content.startsWith("@ai")).toBe(true);
    expect(body.userId).toBe("u1");
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("flips thinking on for the duration of the flow and off at the end", async () => {
    let stillThinkingDuringCall = false;
    stubFetch(() => {
      stillThinkingDuringCall = thinkingNow;
      return { items: [] };
    });
    let thinkingNow = false;
    const seen: boolean[] = [];
    await sendWithAi({
      meetingId: "mtg-9",
      userId: "u1",
      content: "q",
      token: TOKEN,
      onMessages: () => {},
      onThinking: (v) => {
        thinkingNow = v;
        seen.push(v);
      },
    });
    expect(seen[0]).toBe(true);
    expect(seen[seen.length - 1]).toBe(false);
    // While the network call was in flight the UI was already "thinking".
    expect(stillThinkingDuringCall).toBe(true);
  });

  it("refreshes messages after posting so the AI reply lands in state", async () => {
    const f = stubFetch((url) => {
      if (String(url).includes("/messages") && String(url.split("?").pop() !== "")) {
        // M16-X2: pagination envelope — the refresh unwraps `items`.
        return {
          items: [{ userId: "ai-participant", content: "model says hi" }],
          hasMore: false,
        };
      }
      return { items: [], hasMore: false };
    });
    let finalMessages: MeetingMsg[] = [];
    await sendWithAi({
      meetingId: "mtg-9",
      userId: "u1",
      content: "q",
      token: TOKEN,
      onMessages: (msgs) => {
        finalMessages = msgs;
      },
      onThinking: () => {},
    });
    void f;
    expect(finalMessages.some((m) => m.userId === "ai-participant")).toBe(true);
  });

  it("clears thinking even when the send fails", async () => {
    const f = vi.fn(async () => {
      throw new Error("network down");
    });
    vi.stubGlobal("fetch", f);
    const seen: boolean[] = [];
    await expect(
      sendWithAi({
        meetingId: "mtg-9",
        userId: "u1",
        content: "q",
        token: TOKEN,
        onMessages: () => {},
        onThinking: (v) => seen.push(v),
      }),
    ).rejects.toThrow("network down");
    expect(seen[seen.length - 1]).toBe(false);
  });
});

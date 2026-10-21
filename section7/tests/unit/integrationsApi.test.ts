// Part 49.003 (session 12): the chat format is an explicit choice sent with the create request.
import { afterEach, describe, expect, it, vi } from "vitest";
import { createWebhook } from "../../app/settings/integrations/integrationsApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubCreate(seen: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      seen.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ id: "w1", signingSecret: "s" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

describe("createWebhook payload format", () => {
  it("defaults to the signed JSON envelope", async () => {
    const seen: unknown[] = [];
    stubCreate(seen);
    await createWebhook("t1", "https://hooks.example.test/x", ["run.paused"], "tok");
    expect(seen[0]).toEqual({
      endpointUrl: "https://hooks.example.test/x",
      eventTypes: ["run.paused"],
      payloadFormat: "harness",
    });
  });

  it("sends slack only when chosen", async () => {
    const seen: unknown[] = [];
    stubCreate(seen);
    await createWebhook("t1", "https://hooks.slack.com/services/x", ["run.paused"], "tok", "slack");
    expect((seen[0] as { payloadFormat: string }).payloadFormat).toBe("slack");
  });
});

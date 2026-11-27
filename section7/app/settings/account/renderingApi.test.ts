// Task 13: the rendering client sends the bearer token and the exact PUT body the API expects.
import { afterEach, describe, expect, it, vi } from "vitest";
import { IDENTITY_BASE } from "@/lib/api";
import { getRendering, putRendering } from "./renderingApi";

afterEach(() => vi.unstubAllGlobals());

const reply = (body: unknown) =>
  vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify(body) }));
const call = (f: ReturnType<typeof reply>) => f.mock.calls[0] as unknown as [string, RequestInit];

describe("renderingApi", () => {
  it("GETs the current preference with the session token", async () => {
    const f = reply({ userId: "u", rendering: "technical" });
    vi.stubGlobal("fetch", f);
    await expect(getRendering("tok")).resolves.toEqual({ userId: "u", rendering: "technical" });
    const [url, init] = call(f);
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/me/rendering`);
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok");
    expect(init.method).toBeUndefined();
  });

  it("PUTs the chosen rendering as { rendering: 'plain' }", async () => {
    const f = reply({ userId: "u", rendering: "plain" });
    vi.stubGlobal("fetch", f);
    await expect(putRendering("tok", "plain")).resolves.toEqual({ userId: "u", rendering: "plain" });
    const [url, init] = call(f);
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/me/rendering`);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ rendering: "plain" });
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok");
  });
});

// M12-G9: checkpoint client fetchers — URL shapes, bearer header, raw-text diff.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { listCheckpoints, getCheckpointDiff } from "@/lib/api";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function jsonRes(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("listCheckpoints", () => {
  it("GETs the sandbox checkpoints route with bearer auth and returns items", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes({
        projectId: "p1",
        items: [
          { sha: "a".repeat(40), message: "checkpoint: post-edit step-1", createdAt: "2026-08-24T00:00:00Z", taskId: "t1" },
        ],
        nextCursor: null,
      }),
    );

    const list = await listCheckpoints("p1", "tok-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/sandbox/p1/checkpoints");
    expect(init.headers.authorization).toBe("Bearer tok-1");
    expect(list.items).toHaveLength(1);
    expect(list.items[0].message).toContain("post-edit");
  });

  it("URL-encodes the projectId", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ projectId: "a b", items: [], nextCursor: null }));
    await listCheckpoints("a b", "tok");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/sandbox/a%20b/checkpoints");
  });
});

describe("getCheckpointDiff", () => {
  it("returns the raw text/plain body (not JSON-parsed)", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("diff --git a/x.py b/x.py\n+print(1)\n", {
        status: 200,
        headers: { "content-type": "text/plain; charset=utf-8" },
      }),
    );
    const text = await getCheckpointDiff("p1", `${"b".repeat(40)}`, "tok-1");
    expect(text.startsWith("diff --git")).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain(`/checkpoints/${"b".repeat(40)}/diff`);
    expect(init.headers.authorization).toBe("Bearer tok-1");
  });

  it("throws ApiError-shaped failures on non-2xx", async () => {
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 404 }));
    await expect(getCheckpointDiff("p1", "deadbeef1234", "tok")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
    });
  });
});

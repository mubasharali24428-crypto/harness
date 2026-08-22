import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MEETING_BASE,
  REVIEW_BASE,
  approveBrief,
  approvePr,
  createMeeting,
  generateBrief,
  getBrief,
  getMessages,
  getReviewData,
  invokeAi,
  postMessage,
  requestChanges,
} from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubOk(responseBody: unknown) {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(responseBody),
  }));
}

const TOKEN = "tok-123";

describe("meeting clients (Section 6)", () => {
  it("createMeeting posts projectId+title under /api/v1/meetings with bearer", async () => {
    const f = stubOk({ id: "mtg-1", projectId: "p1", title: "t" });
    vi.stubGlobal("fetch", f);
    await createMeeting("p1", "Kickoff", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${MEETING_BASE}/api/v1/meetings`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ projectId: "p1", title: "Kickoff" });
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("postMessage sends userId + content (S6 owns identity-in-body by design)", async () => {
    const f = stubOk([]);
    vi.stubGlobal("fetch", f);
    await postMessage("mtg-9", "u1", "hello", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${MEETING_BASE}/api/v1/meetings/mtg-9/messages`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ userId: "u1", content: "hello" });
  });

  it("invokeAi hits the /ai route", async () => {
    const f = stubOk({ reply: "ok" });
    vi.stubGlobal("fetch", f);
    await invokeAi("mtg-9", "u1", "status?", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${MEETING_BASE}/api/v1/meetings/mtg-9/ai`);
    expect(JSON.parse(String(init.body))).toEqual({ userId: "u1", content: "status?" });
  });

  it("generateBrief posts to the brief collection endpoint", async () => {
    const f = stubOk({ id: "brf-1", requirements: [] });
    vi.stubGlobal("fetch", f);
    await generateBrief("mtg-9", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${MEETING_BASE}/api/v1/meetings/mtg-9/brief`);
    expect(init.method).toBe("POST");
  });

  it("getBrief reads the brief resource", async () => {
    const f = stubOk({ id: "brf-1", requirements: [] });
    vi.stubGlobal("fetch", f);
    await getBrief("mtg-9", TOKEN);
    const [url] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${MEETING_BASE}/api/v1/meetings/mtg-9/brief`);
  });

  it("approveBrief posts userId to the brief approval path", async () => {
    const f = stubOk({ id: "brf-1", status: "approved" });
    vi.stubGlobal("fetch", f);
    await approveBrief("mtg-9", "brf-1", "u1", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${MEETING_BASE}/api/v1/meetings/mtg-9/brief/brf-1/approve`);
    expect(JSON.parse(String(init.body))).toEqual({ userId: "u1" });
  });
});

describe("review clients (Section 8)", () => {
  it("getReviewData GETs the review resource with bearer", async () => {
    const f = stubOk({ prId: "7" });
    vi.stubGlobal("fetch", f);
    await getReviewData("7", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${REVIEW_BASE}/api/v1/reviews/7`);
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("approvePr keeps S8's reviewer_user_id body field (route contract)", async () => {
    const f = stubOk({});
    vi.stubGlobal("fetch", f);
    await approvePr("7", "rev-u1", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${REVIEW_BASE}/api/v1/reviews/7/approve`);
    expect(init.method).toBe("POST");
    // S8's ApproveRequest literally requires reviewer_user_id — wire shape, not drift.
    expect(JSON.parse(String(init.body))).toEqual({ reviewer_user_id: "rev-u1" });
  });

  it("requestChanges posts comments + reviewer_user_id", async () => {
    const f = stubOk({});
    vi.stubGlobal("fetch", f);
    await requestChanges("7", [{ path: "a.py", note: "fix" }], "rev-u1", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${REVIEW_BASE}/api/v1/reviews/7/request-changes`);
    expect(JSON.parse(String(init.body))).toEqual({
      comments: [{ path: "a.py", note: "fix" }],
      reviewer_user_id: "rev-u1",
    });
  });
});

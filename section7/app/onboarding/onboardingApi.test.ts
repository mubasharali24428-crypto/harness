import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, parseApiError } from "@/lib/api";
import {
  ChecklistItemDto,
  completedCount,
  formatProgressLabel,
  getChecklist,
  progressPct,
} from "./onboardingApi";

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

const ITEMS: ChecklistItemDto[] = [
  { id: "connect_github", title: "Connect GitHub", description: "OAuth into GitHub.", done: false },
  { id: "other_integrations", title: "Other integrations", description: null, done: true },
  { id: "create_team", title: "Create a team", done: false },
];

describe("onboarding checklist client (M10-B3)", () => {
  it("getChecklist GETs /api/v1/onboarding/checklist and parses items", async () => {
    const f = stubOk({
      items: [
        { id: "connect_github", title: "Connect GitHub", description: "d1", done: false },
        { id: "guided_demo", title: "Open the guided demo", description: "d2", done: true },
      ],
    });
    vi.stubGlobal("fetch", f);
    const dto = await getChecklist();
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit | undefined];
    expect(url).toBe("http://localhost:8010/api/v1/onboarding/checklist");
    expect(init?.method).toBeUndefined(); // plain GET — route takes no auth today
    expect(dto.items).toHaveLength(2);
    expect(dto.items[1].done).toBe(true);
  });

  it("honors an explicit base override (deploy wiring)", async () => {
    const f = stubOk({ items: [] });
    vi.stubGlobal("fetch", f);
    await getChecklist("http://onboarding.internal:9000");
    const [url] = f.mock.calls[0] as unknown as [string];
    expect(url).toBe("http://onboarding.internal:9000/api/v1/onboarding/checklist");
  });

  it("surfaces the contract error envelope as ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 503,
        text: async () =>
          JSON.stringify({ error: { code: "SERVICE_UNAVAILABLE", message: "onboarding down" } }),
      })),
    );
    await expect(getChecklist()).rejects.toMatchObject({
      name: "ApiError",
      code: "SERVICE_UNAVAILABLE",
      status: 503,
    } satisfies Partial<ApiError>);
  });

  it("maps FastAPI-style error bodies too (parseApiError parity)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 404,
        text: async () =>
          JSON.stringify({ detail: { error: { code: "NOT_FOUND", message: "no such route" } } }),
      })),
    );
    try {
      await getChecklist();
      throw new Error("expected rejection");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).code).toBe("NOT_FOUND");
      expect((err as ApiError).status).toBe(404);
    }
    // sanity: the shared parser is the one doing the mapping
    expect(parseApiError(404, { detail: "plain string" })).toEqual({
      message: "plain string",
      code: "UNKNOWN",
    });
  });

  it("tolerates non-JSON error bodies", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, text: async () => "<html>boom</html>" })),
    );
    await expect(getChecklist()).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
    } satisfies Partial<ApiError>);
  });
});

describe("checklist progress helpers", () => {
  it("completedCount counts done rows only", () => {
    expect(completedCount(ITEMS)).toBe(1);
    expect(completedCount([])).toBe(0);
    expect(completedCount([...ITEMS, { id: "x", title: "X", done: true }])).toBe(2);
  });

  it("progressPct clamps to [0,100] and treats empty checklist as 0%", () => {
    expect(progressPct([])).toBe(0); // never NaN
    expect(progressPct(ITEMS)).toBe(33); // 1/3 → rounded down
    expect(progressPct([{ id: "a", title: "A", done: true }])).toBe(100);
  });

  it("formatProgressLabel renders the n/m readout", () => {
    expect(formatProgressLabel(ITEMS)).toBe("1/3 steps complete");
    expect(formatProgressLabel([])).toBe("0/0 steps complete");
  });
});

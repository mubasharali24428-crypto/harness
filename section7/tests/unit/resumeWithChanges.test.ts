// W2 (N04) — "retry with changes" is a two-call sequence and the order is the
// whole feature. The executor drains guidance with `guidance_since` when it
// starts, so a resume that fires first reads nothing and the operator's
// correction is silently ignored — a fake affordance with a real button.

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, resumeWithChanges } from "@/lib/api";

type Call = { url: string; init?: RequestInit };

function stubSequence(bodies: unknown[]): Call[] {
  const calls: Call[] = [];
  let i = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const body = bodies[Math.min(i, bodies.length - 1)];
      i += 1;
      return { ok: true, status: 200, text: async () => JSON.stringify(body) };
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("resumeWithChanges", () => {
  it("records the change before it resumes the run", async () => {
    const calls = stubSequence([{ ok: true }, { runId: "r1", state: "executing" }]);
    await resumeWithChanges("r1", "use the existing helper instead", "tok");

    expect(calls).toHaveLength(2);
    expect(calls[0].url).toContain("/runs/r1/redirect");
    expect(calls[1].url).toContain("/runs/r1/resume");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      kind: "guidance",
      content: "use the existing helper instead",
    });
  });

  it("trims what it records", async () => {
    const calls = stubSequence([{ ok: true }, { runId: "r1", state: "executing" }]);
    await resumeWithChanges("r1", "  pick option B  ", "tok");
    expect(JSON.parse(String(calls[0].init?.body)).content).toBe("pick option B");
  });

  it("refuses an empty change instead of quietly becoming a plain resume", async () => {
    const calls = stubSequence([{ ok: true }]);
    await expect(resumeWithChanges("r1", "   ", "tok")).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(0);
  });

  it("does not resume when recording the change failed", async () => {
    const calls: Call[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return {
          ok: false,
          status: 403,
          text: async () => JSON.stringify({ error: { code: "DENIED", message: "denied" } }),
        };
      }),
    );
    await expect(resumeWithChanges("r1", "do it differently", "tok")).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/redirect");
  });
});

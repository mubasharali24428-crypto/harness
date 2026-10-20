// Parts 31/58: the client for POST /api/v1/account/erasure and the words a person sees when it
// refuses. The server's two refusals already carry human wording; the rest are mapped here.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, IDENTITY_BASE } from "@/lib/api";
import { eraseAccount, erasureProblem, runErasure } from "./accountApi";

afterEach(() => vi.unstubAllGlobals());

const reply = (status: number, body: unknown) =>
  vi.fn(async () => ({ ok: status < 400, status, text: async () => JSON.stringify(body) }));
const refusal = (status: number, code: string, message: string) =>
  reply(status, { detail: { error: { code, message } } });

describe("eraseAccount", () => {
  it("POSTs the confirmation email with the bearer token and returns the server's answer", async () => {
    const f = reply(200, { erased: true });
    vi.stubGlobal("fetch", f);
    await expect(eraseAccount("me@a.io", "tok")).resolves.toEqual({ erased: true });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/account/erasure`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ confirmEmail: "me@a.io" });
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok");
  });

  it("rejects with the server's code so the page can tell the refusals apart", async () => {
    vi.stubGlobal("fetch", refusal(409, "SOLE_ADMIN", "you are the only admin"));
    await expect(eraseAccount("me@a.io", "tok")).rejects.toMatchObject({ code: "SOLE_ADMIN", status: 409 });
  });
});

describe("erasureProblem", () => {
  const err = (status: number, code: string, message: string) => new ApiError(message, code, status);

  it("passes the two refusals through in the server's own words", () => {
    const sole = "you are the only admin or owner of 1 team(s) and 0 organization(s): hand them over or delete them first";
    const held = "2 run(s) you took part in are under a retention hold; ask again after the hold is released";
    expect(erasureProblem(err(409, "SOLE_ADMIN", sole))).toContain(sole);
    expect(erasureProblem(err(409, "ERASURE_ON_HOLD", held))).toContain(held);
  });

  it("says a refusal changed nothing", () => {
    expect(erasureProblem(err(409, "SOLE_ADMIN", "x"))).toMatch(/nothing was changed/i);
    expect(erasureProblem(err(409, "ERASURE_ON_HOLD", "x"))).toMatch(/nothing was changed/i);
    expect(erasureProblem(err(422, "VALIDATION_ERROR", "confirmEmail must be ..."))).toMatch(/nothing was changed/i);
  });

  it("explains a wrong email without echoing the technical message", () => {
    const text = erasureProblem(err(422, "VALIDATION_ERROR", "confirmEmail must be the email of this account"));
    expect(text).toMatch(/not the email address of this account/i);
    expect(text).not.toMatch(/confirmEmail/);
  });

  it("does not claim to know the outcome when the request failed some other way", () => {
    const text = erasureProblem(err(500, "UNKNOWN", "Request failed with status 500"));
    expect(text).toMatch(/may not have been erased/i);
    expect(text).not.toMatch(/nothing was changed/i);
    expect(erasureProblem(new TypeError("fetch failed"))).toMatch(/could not reach the server/i);
  });

  it("sends a signed-out person to sign in again", () => {
    expect(erasureProblem(err(401, "UNAUTHORIZED", "no"))).toMatch(/sign in again/i);
  });
});

describe("runErasure", () => {
  const ok = vi.fn(async () => ({ erased: true }));
  const refuse = vi.fn(async () => { throw new ApiError("two runs on hold", "ERASURE_ON_HOLD", 409); });

  it("trims the typed email, ends the local session, and reports done", async () => {
    const clear = vi.fn();
    ok.mockClear();
    await expect(runErasure("  me@a.io ", "tok", { erase: ok, clear })).resolves.toEqual({ done: true, problem: null });
    expect(ok).toHaveBeenCalledWith("me@a.io", "tok");
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it("keeps the session when the server refuses, and says why", async () => {
    const clear = vi.fn();
    const out = await runErasure("me@a.io", "tok", { erase: refuse, clear });
    expect(clear).not.toHaveBeenCalled();
    expect(out.done).toBe(false);
    expect(out.problem).toMatch(/two runs on hold.*nothing was changed/i);
  });

  it("does not call the server without a session", async () => {
    const erase = vi.fn();
    const out = await runErasure("me@a.io", undefined, { erase, clear: vi.fn() });
    expect(erase).not.toHaveBeenCalled();
    expect(out).toEqual({ done: false, problem: expect.stringMatching(/not signed in/i) });
  });
});

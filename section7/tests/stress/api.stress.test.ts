import { describe, it, expect } from "vitest";
import { parseApiError } from "@/lib/api";

describe("parseApiError - Comprehensive Edge Cases", () => {
  it("parses valid standardized error envelopes", () => {
    const body = { error: { code: "UNAUTHORIZED", message: "Invalid credentials" } };
    expect(parseApiError(401, body)).toEqual({ code: "UNAUTHORIZED", message: "Invalid credentials" });
  });

  it("parses FastAPI wrapped HTTPException detail format", () => {
    const body = { detail: { error: { code: "PLAN_NOT_FOUND", message: "No plan found" } } };
    expect(parseApiError(404, body)).toEqual({ code: "PLAN_NOT_FOUND", message: "No plan found" });
  });

  it("parses FastAPI string detail format", () => {
    const body = { detail: "Not authenticated" };
    expect(parseApiError(401, body)).toEqual({ code: "UNKNOWN", message: "Not authenticated" });
  });

  it("handles non-object bodies safely", () => {
    expect(parseApiError(502, "Bad Gateway")).toEqual({ code: "UNKNOWN", message: "Request failed with status 502" });
    expect(parseApiError(404, 123)).toEqual({ code: "UNKNOWN", message: "Request failed with status 404" });
    expect(parseApiError(500, null)).toEqual({ code: "UNKNOWN", message: "Request failed with status 500" });
    expect(parseApiError(500, undefined)).toEqual({ code: "UNKNOWN", message: "Request failed with status 500" });
  });

  it("handles empty object bodies", () => {
    expect(parseApiError(400, {})).toEqual({ code: "UNKNOWN", message: "Request failed with status 400" });
  });

  it("handles partial error object", () => {
    expect(parseApiError(400, { error: {} })).toEqual({ code: "UNKNOWN", message: "Request failed with status 400" });
    expect(parseApiError(400, { error: { code: "SOME_CODE" } })).toEqual({
      code: "SOME_CODE",
      message: "Request failed with status 400",
    });
  });
});

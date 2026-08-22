import { describe, it, expect } from "vitest";
import { parseApiError } from "@/lib/api";

describe("parseApiError", () => {
  it("extracts the frozen error envelope shape", () => {
    const result = parseApiError(400, { error: { code: "INVALID_STATE", message: "bad transition" } });
    expect(result).toEqual({ code: "INVALID_STATE", message: "bad transition" });
  });

  it("falls back gracefully when the body doesn't match the envelope", () => {
    const result = parseApiError(500, null);
    expect(result.code).toBe("UNKNOWN");
    expect(result.message).toContain("500");
  });

  // Regression: sections 4 and 6 both raise HTTPException(detail={"error": {...}}),
  // which FastAPI's default handler wraps as {"detail": {"error": {...}}} on the wire -
  // not the bare {"error": {...}} the convention doc describes. Confirmed live via curl
  // against both running servers.
  it("unwraps FastAPI's detail-wrapped error envelope (section4, section6's real shape)", () => {
    const result = parseApiError(404, {
      detail: { error: { code: "PLAN_NOT_FOUND", message: "No plan proposed yet for run 'x'." } },
    });
    expect(result).toEqual({ code: "PLAN_NOT_FOUND", message: "No plan proposed yet for run 'x'." });
  });

  // Regression: section1 raises HTTPException(detail="plain string"), FastAPI's own
  // default shape with no envelope at all. Confirmed live via curl against section1.
  it("reads a plain string detail (section1's real shape)", () => {
    const result = parseApiError(401, { detail: "invalid or expired session" });
    expect(result).toEqual({ code: "UNKNOWN", message: "invalid or expired session" });
  });
});

import { describe, expect, it } from "vitest";
import { ApiError, displayErrorMessage, friendlyErrorMessage } from "@/lib/api";

// M12-E2 friendly errors: known backend codes must become human sentences with
// a next-step hint; unknown codes fall through to the generic message plus a
// correlation hint. No dev jargon (service names, ports, storage keys) may
// appear in user-facing strings.

const KNOWN_CODES = [
  "RATE_LIMITED",
  "RATE_LIMIT_EXCEEDED",
  "OWNERSHIP_REQUIRED",
  "PLAN_NOT_APPROVED",
  "INVALID_STATE",
  "NO_BRIEF",
  "PLAN_MISMATCH",
  "PLAN_NOT_FOUND",
  "NOT_FOUND",
  "INVALID_CODE",
  "EXPIRED",
  "LOCKED",
  "ROLE_DENIED",
  "SECURITY_POLICY_VIOLATION",
] as const;

describe("friendlyErrorMessage map", () => {
  it("maps every code the backends actually emit to a sentence with a next-step hint", () => {
    for (const code of KNOWN_CODES) {
      const msg = friendlyErrorMessage(code);
      expect(msg, `missing copy for ${code}`).toBeTruthy();
      expect(msg!.endsWith(".")).toBe(true);
      // Next-step hint: the second sentence must tell the user what to do.
      const sentences = msg!.split(/(?<=\.)\s+/);
      expect(sentences.length).toBeGreaterThanOrEqual(2);
    }
  });

  it("returns null for unknown codes so callers use the generic fallback", () => {
    expect(friendlyErrorMessage("TOTALLY_NEW_CODE")).toBeNull();
    expect(friendlyErrorMessage("")).toBeNull();
    // Case-sensitive: only exact contract codes map.
    expect(friendlyErrorMessage("rate_limited")).toBeNull();
  });

  it("keeps dev jargon out of user-facing copy", () => {
    for (const code of KNOWN_CODES) {
      const msg = friendlyErrorMessage(code) as string;
      expect(msg.toLowerCase()).not.toContain("s6");
      expect(msg.toLowerCase()).not.toContain("s8");
      expect(msg).not.toMatch(/:\d{4}/); // no port numbers
      expect(msg).not.toContain("harness.");
      expect(msg).not.toContain("/api/");
      expect(msg).not.toMatch(/\b(section|service)\b/i);
    }
  });
});

describe("displayErrorMessage", () => {
  it("uses the friendly copy for known codes", () => {
    const err = new ApiError("PLAN_NOT_APPROVED: raw server words", "PLAN_NOT_APPROVED", 409);
    expect(displayErrorMessage(err)).toBe(friendlyErrorMessage("PLAN_NOT_APPROVED"));
  });

  it("appends a correlation hint to unmapped errors", () => {
    const err = new ApiError("Something exploded", "MYSTERY_CODE", 500);
    const out = displayErrorMessage(err);
    expect(out).toContain("Something exploded");
    expect(out).toMatch(/contact support/i);
  });

  it("handles non-Error throwables", () => {
    expect(displayErrorMessage("plain string failure")).toMatch(/contact support/i);
  });
});

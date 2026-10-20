// Part 24.010: every refusal the collaboration API documents has a plain sentence in the UI.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { PLAIN_ERRORS, plainError } from "./collabModel";

const contract = readFileSync(resolve(process.cwd(), "../docs/collaboration-contract.md"), "utf8");
const documented = [...contract.split("## 9. Error index")[1].matchAll(/^\| `([A-Z_]+)` \|/gm)].map((m) => m[1]);

describe("plain-language errors", () => {
  it("covers every code in the contract's error index and nothing else", () => {
    expect(documented.length).toBeGreaterThan(20);
    expect(Object.keys(PLAIN_ERRORS).sort()).toEqual([...documented].sort());
  });

  it("speaks to a non-engineer: no codes, no underscores, one short sentence or two", () => {
    for (const [code, text] of Object.entries(PLAIN_ERRORS)) {
      expect(text, code).not.toMatch(/[A-Z]{2,}_|_[A-Z]{2,}|\b[A-Z_]{4,}\b/);
      expect(text, code).toMatch(/[.]$/);
      expect(text.length, code).toBeLessThan(120);
    }
  });

  it("maps an ApiError by its code and leaves unknown errors to the caller", () => {
    expect(plainError(new ApiError("only the session leader may do this", "NOT_LEADER", 403)))
      .toBe("Only the person leading this run can do that.");
    expect(plainError(new ApiError("boom", "SOMETHING_ELSE", 500))).toBeNull();
    expect(plainError(new Error("network down"))).toBeNull();
    expect(plainError(null)).toBeNull();
    expect(plainError({ code: "constructor" })).toBeNull(); // not an own key of the table
  });

  it("is used at both places the panel turns a failure into text", () => {
    const panel = readFileSync(resolve(__dirname, "sharedRunPanel.tsx"), "utf8");
    expect(panel.match(/plainError\(/g)?.length).toBe(2);
  });
});

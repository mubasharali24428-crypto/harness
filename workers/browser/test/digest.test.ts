import { describe, expect, it } from "vitest";
import { canonicalJson, digestOf, sha256Hex } from "../src/digest.js";

describe("digest helpers", () => {
  it("canonicalizes object key order", () => {
    expect(canonicalJson({ b: 1, a: [2, { d: 4, c: 3 }] })).toBe('{"a":[2,{"c":3,"d":4}],"b":1}');
  });

  it("digests reordered objects identically", () => {
    expect(digestOf({ x: 1, y: { b: 2, a: 3 } })).toBe(digestOf({ y: { a: 3, b: 2 }, x: 1 }));
  });

  it("sha256Hex is stable", () => {
    expect(sha256Hex("hello")).toBe(
      "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    );
  });
});

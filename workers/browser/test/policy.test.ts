import { describe, expect, it } from "vitest";
import { assertNavigationAllowed, checkFinalUrl, classifyUrl } from "../src/policy.js";
import { WorkerError } from "../src/types.js";

const policy = { allowedOrigins: ["http://127.0.0.1:4100"] };

function expectCode(fn: () => unknown, code: string): void {
  try {
    fn();
    expect.unreachable("expected a WorkerError");
  } catch (error) {
    expect(error).toBeInstanceOf(WorkerError);
    expect((error as WorkerError).code).toBe(code);
  }
}

describe("navigation policy", () => {
  it("allows an exact allowed origin", () => {
    expect(assertNavigationAllowed("http://127.0.0.1:4100/form", policy).origin).toBe(
      "http://127.0.0.1:4100",
    );
  });

  it("denies loopback origins that were not granted (SSRF class)", () => {
    expectCode(() => assertNavigationAllowed("http://127.0.0.1:4101/x", policy), "SSRF_DENIED");
  });

  it("denies non-http schemes", () => {
    expectCode(() => assertNavigationAllowed("file:///etc/passwd", policy), "EGRESS_DENIED");
  });

  it("denies credentials embedded in the URL", () => {
    expectCode(
      () => assertNavigationAllowed("http://user:pw@127.0.0.1:4100/x", policy),
      "SSRF_DENIED",
    );
  });

  it("denies the cloud metadata endpoint explicitly", () => {
    expectCode(
      () => assertNavigationAllowed("http://169.254.169.254/latest/meta-data", policy),
      "SSRF_DENIED",
    );
  });

  it("denies public hosts outside the allowlist (egress class)", () => {
    expectCode(() => assertNavigationAllowed("https://example.com/", policy), "EGRESS_DENIED");
  });

  it("classifies private ranges", () => {
    expect(classifyUrl("http://10.0.0.5/x").isPrivateNetwork).toBe(true);
    expect(classifyUrl("http://192.168.1.1/x").isPrivateNetwork).toBe(true);
    expect(classifyUrl("http://172.16.0.1/x").isPrivateNetwork).toBe(true);
    expect(classifyUrl("https://example.com/x").isPrivateNetwork).toBe(false);
  });

  it("catches redirects landing on a disallowed origin", () => {
    expectCode(
      () => checkFinalUrl("http://127.0.0.1:4101/landing", policy),
      "SSRF_DENIED",
    );
    expect(() => checkFinalUrl("about:blank", policy)).not.toThrow();
    expect(() => checkFinalUrl("http://127.0.0.1:4100/ok", policy)).not.toThrow();
  });
});

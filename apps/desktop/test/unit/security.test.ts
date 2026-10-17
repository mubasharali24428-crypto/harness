import { describe, expect, it } from "vitest";
import {
  WEB_PREFERENCES, contentSecurityPolicy, externalOpenAllowed, isAllowedNavigation,
  parseDeepLink, permissionDecision, resolveAppOrigin,
} from "../../src/security";

const app = new URL("https://harness.example.com");

describe("web preferences", () => {
  it("are locked down", () => {
    expect(WEB_PREFERENCES).toMatchObject({ contextIsolation: true, nodeIntegration: false, sandbox: true,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false });
  });
});

describe("app origin", () => {
  it("accepts https and loopback http only, never embedded credentials", () => {
    expect(resolveAppOrigin("https://harness.example.com").origin).toBe("https://harness.example.com");
    expect(resolveAppOrigin(undefined).origin).toBe("http://localhost:3000");
    expect(resolveAppOrigin("http://127.0.0.1:3000").hostname).toBe("127.0.0.1");
    for (const bad of ["http://harness.example.com", "file:///etc/passwd", "https://u:p@h.example.com", "javascript:alert(1)"]) {
      expect(() => resolveAppOrigin(bad), bad).toThrow();
    }
  });
});

describe("navigation", () => {
  it("allows only the app origin (not look-alikes)", () => {
    expect(isAllowedNavigation("https://harness.example.com/review/1", app)).toBe(true);
    for (const bad of ["https://harness.example.com.evil.com/", "http://harness.example.com/", "https://evil.com/",
      "file:///etc/passwd", "javascript:alert(1)", "not a url", "https://harness.example.com@evil.com/"]) {
      expect(isAllowedNavigation(bad, app), bad).toBe(false);
    }
  });
  it("external opens are https only", () => {
    expect(externalOpenAllowed("https://docs.example.com/x")).toBe(true);
    for (const bad of ["http://x.com", "file:///x", "javascript:1", "harness://run/1", "https://u:p@x.com", "ssh://x"]) {
      expect(externalOpenAllowed(bad), bad).toBe(false);
    }
  });
});

describe("deep links", () => {
  it("maps review/run links to app routes", () => {
    expect(parseDeepLink("harness://review/cs_123", app)).toBe("https://harness.example.com/review/cs_123");
    expect(parseDeepLink("harness://run/run-9", app)).toBe("https://harness.example.com/runs/run-9");
  });
  it("drops anything else, including traversal, credentials, query/hash and unknown routes", () => {
    for (const bad of ["harness://review/../admin", "harness://review/a/b", "harness://admin/x", "harness://review/",
      "harness://review/x?token=abc", "harness://review/x#frag", "harness://u:p@review/x", "https://review/x",
      "harness://review/%2e%2e", "harness://review/" + "a".repeat(65), "garbage", "harness://run/a b"]) {
      expect(parseDeepLink(bad, app), bad).toBeNull();
    }
  });
});

describe("policy", () => {
  it("CSP forbids objects/base/framing and inline script", () => {
    const csp = contentSecurityPolicy(app);
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).not.toContain("unsafe-inline");
    expect(csp).not.toContain("unsafe-eval");
  });
  it("denies every permission request", () => {
    for (const p of ["media", "geolocation", "notifications", "clipboard-read", "openExternal"]) {
      expect(permissionDecision(p)).toBe(false);
    }
  });
});

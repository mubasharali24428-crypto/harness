// F-gap closure — static render smoke for /auth/sso/callback (node-env vitest,
// react-dom/server emits markup, same technique as migratedPagesSmoke).
import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

describe("SSO callback page (F-gap closure)", () => {
  it("renders the completing state through the DS primitives", async () => {
    const { default: SsoCallback } = await import(
      "../../app/auth/sso/callback/page"
    );
    const html = renderToStaticMarkup(React.createElement(SsoCallback));
    expect(html).toContain('data-testid="sso-callback"');
    expect(html).toContain("Completing sign-in");
    expect(html).toMatch(/<h2[^>]*>HARNESS<\/h2>/);
  });
});

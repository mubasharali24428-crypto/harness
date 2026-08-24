// M14-F1b (raw-03 P2) — AppShell keyboard/focus structure tests.
// Node-env protocol: renderToStaticMarkup + vi.mock for the browser-only
// pieces (next/navigation router, localStorage-backed auth helpers). The
// assertions target exactly what F1b shipped: skip link FIRST in the shell,
// pointing at the id="main-content" landmark.
import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {} }),
}));
vi.mock("@/lib/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth")>();
  return {
    ...actual,
    getToken: () => null, // no session → shell renders its signed-out state
    loadSession: () => null,
    getProjectId: () => null,
    getRunId: () => null,
  };
});
// No session → no projects fetch; keep listProjects from touching the network
// even if a future change calls it before the token check.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, listProjects: () => Promise.resolve([]) };
});

import AppShell from "@/components/AppShell";

describe("AppShell skip link (M14-F1b)", () => {
  const html = renderToStaticMarkup(<AppShell active="/runs">content-marker</AppShell>);

  it("renders a 'Skip to content' link as the FIRST element of the shell", () => {
    const linkStart = html.indexOf('<a class="skip-link"');
    expect(linkStart).toBeGreaterThanOrEqual(0);
    const first = html.slice(0, linkStart);
    // Nothing interactive renders before it — no <a href, <button, or <select
    // may precede the skip link in DOM order.
    expect(first).not.toMatch(/<(a |button|select)/);
  });

  it("targets the #main-content landmark", () => {
    expect(html).toContain('href="#main-content"');
    expect(html).toContain('id="main-content"');
  });

  it("main carries tabindex=-1 so it can receive programmatic focus", () => {
    expect(html).toContain('<main id="main-content" tabindex="-1" class="content">');
  });

  it("still renders page content inside the main region", () => {
    const mainStart = html.indexOf('id="main-content"');
    expect(html.slice(mainStart)).toContain("content-marker");
  });
});

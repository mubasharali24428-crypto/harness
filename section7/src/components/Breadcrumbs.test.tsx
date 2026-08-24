// M13-G5b — breadcrumbs (raw-14 DX batch). Deep pages get a visible way back:
// Command Center (/) → Settings (/settings) → current page.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
// Alias-free import: vitest resolves "@/..." via its own alias map, but this
// spec lives under app/ like the other component tests — keep one convention.
import { BreadcrumbTrail, settingsCrumbs } from "./Breadcrumbs";

describe("BreadcrumbTrail", () => {
  it("renders real anchor links for crumbs with an href", () => {
    const html = renderToStaticMarkup(
      <BreadcrumbTrail items={settingsCrumbs("Team")} />,
    );
    expect(html).toContain('href="/"');
    expect(html).toContain('href="/settings"');
  });

  it("labels the trail for assistive tech and marks the current page", () => {
    const html = renderToStaticMarkup(
      <BreadcrumbTrail items={settingsCrumbs("Provider keys")} />,
    );
    expect(html).toContain('aria-label="Breadcrumb"');
    expect(html).toContain('aria-current="page"');
  });

  it("never links the current page to itself", () => {
    const html = renderToStaticMarkup(
      <BreadcrumbTrail items={[{ label: "Here", href: "/here" }, { label: "Now" }]} />,
    );
    // The last crumb is plain text, not a link.
    const tail = html.slice(html.indexOf(">Now<") - 40);
    expect(tail).not.toContain("<a ");
  });

  it("shows the separator between crumbs only", () => {
    const html = renderToStaticMarkup(
      <BreadcrumbTrail items={[{ label: "A", href: "/" }, { label: "B", href: "/b" }, { label: "C" }]} />,
    );
    expect(html.split("/").length - 1).toBeGreaterThanOrEqual(2);
    expect(html).toContain(">A</a>");
    expect(html).toContain(">C</span>");
  });

  it("settings trails root at the Command Center and pass through Settings", () => {
    const crumbs = settingsCrumbs("Billing & plan");
    expect(crumbs.map((c) => c.href)).toEqual(["/", "/settings", undefined]);
    expect(crumbs[2].label).toBe("Billing & plan");
  });
});

import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { LandingView } from "./Landing";

const html = renderToStaticMarkup(<LandingView />);

describe("landing page", () => {
  it("leads with the veto, not with autonomy", () => {
    expect(html).toContain("Ship AI-written code.");
    expect(html).toContain("Keep the veto.");
    expect(html).toContain("Private design-partner build");
  });

  it("offers both ways in: sign in and the recorded run", () => {
    expect(html).toContain('href="/login"');
    expect(html).toContain('href="/demo"');
    expect(html).toContain("Watch a recorded run");
  });

  it("states the four controls that the server actually enforces", () => {
    for (const title of ["A plan gate", "Confined execution", "Human acceptance", "Metered spend"]) {
      expect(html).toContain(title);
    }
  });

  it("labels the demo honestly and never promises auto-merge", () => {
    expect(html).toContain("clearly labelled replay");
    expect(html).toContain("auto-merge: never");
    expect(html).not.toContain("auto-merge: on");
  });

  it("keeps the pilot status and legal caveat visible", () => {
    expect(html).toContain("private pilot");
    expect(html).toContain("counsel review");
  });

  it("is a real document landmark structure", () => {
    expect(html).toContain("<main>");
    expect(html).toContain("<header");
    expect(html).toContain("<footer");
    expect(html.match(/<h1/g)?.length).toBe(1);
  });
});

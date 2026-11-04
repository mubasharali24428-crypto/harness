// W18 — /pricing is a CLAIM, so it is tested like one.
//
// The numbers come from lib/pricing.json (compared to the enforcement code by a
// backend test); these tests pin what the page must never get wrong on its own:
// every tier is present, an uncapped cell reads "Unlimited" rather than 0, each
// row says whether it actually refuses, and no dollar amount is invented before
// counsel signs off.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import PricingPage from "./page";
import data from "@/lib/pricing.json";

const html = renderToStaticMarkup(<PricingPage />);

describe("/pricing", () => {
  it("renders every tier as its own card", () => {
    for (const tier of data.tiers) {
      expect(html).toContain(`aria-label="${tier.name} plan"`);
    }
  });

  it("spells an uncapped tier as Unlimited, never as 0", () => {
    expect(html).toContain("Unlimited");
    expect(html).not.toMatch(/>0</);
  });

  it("shows the free caps from the shared data file", () => {
    expect(html).toContain(`>${data.caps.runsPerDay.free}<`);
    expect(html).toContain(`>${data.caps.contextIndexMaxChunks.free}<`);
  });

  it("marks every row as enforced or declared", () => {
    const rows = data.enforced.length + data.declared.length;
    expect(rows).toBe(Object.keys(data.caps).length);
    expect(html.match(/>enforced</g)?.length).toBe(data.enforced.length);
    expect(html.match(/>declared</g)?.length).toBe(data.declared.length);
  });

  it("names the refusal a reader would hit", () => {
    expect(html).toContain("402");
    expect(html).toContain("PLAN_REQUIRED");
  });

  it("invents no amount", () => {
    expect(data.tiers.every((t) => ["$0 forever", "Contact us"].includes(t.price))).toBe(true);
    // exactly one dollar figure exists on the page: the free price
    expect(html.match(/\$/g)?.length).toBe(1);
  });
});

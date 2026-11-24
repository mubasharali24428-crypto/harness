// Task 13: the card offers the three X-D1 choices, keeps the display-only help text,
// and reflects the saved value. Static render, like the other settings cells.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { RenderingCard } from "./renderingCard";
import type { Rendering } from "./renderingApi";

const card = (value: Rendering = "technical") =>
  renderToStaticMarkup(<RenderingCard value={value} onChange={() => {}} onSave={() => {}} />);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

describe("RenderingCard", () => {
  it("offers Technical, Plain and Decision card with the display-only help text", () => {
    const t = text(card());
    for (const label of ["Technical", "Plain", "Decision card"]) expect(t).toContain(label);
    expect(t).toContain("Changes how approvals are shown; does not change permissions.");
  });

  it("marks the saved preference as checked", () => {
    const inputFor = (html: string, value: string) =>
      (html.match(/<input[^>]*\/>/g) ?? []).find((tag) => tag.includes(`value="${value}"`)) ?? "";
    expect(inputFor(card("plain"), "plain")).toContain('checked=""');
    expect(inputFor(card("decision"), "decision")).toContain('checked=""');
  });

  it("describes the radio group with the same help text", () => {
    expect(card()).toContain('aria-describedby="rendering-help"');
    expect(card()).toContain('id="rendering-help"');
  });
});

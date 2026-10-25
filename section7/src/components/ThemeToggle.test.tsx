import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ThemeToggle } from "./ThemeToggle";

describe("theme toggle", () => {
  it("names the NEXT theme for assistive tech", () => {
    const html = renderToStaticMarkup(<ThemeToggle />);
    expect(html).toContain('data-testid="theme-toggle"');
    expect(html).toContain('aria-label="Switch to light theme"');
    expect(html).toContain("☾");
  });

  it("compact mode is icon-only but still labelled", () => {
    const html = renderToStaticMarkup(<ThemeToggle compact />);
    expect(html).toContain('aria-label="Switch to light theme"');
    expect(html).not.toContain("Dark theme</span>");
  });
});

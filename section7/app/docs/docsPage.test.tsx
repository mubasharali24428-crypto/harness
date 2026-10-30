// W18 — /docs is the entry point a stranger tries first: it must contain the
// four real steps (the ones docs/quickstart.md was executed with) and the links
// a reader needs next, without pretending the recorded demo is live.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import DocsPage from "./page";

const html = renderToStaticMarkup(<DocsPage />);

describe("/docs", () => {
  it("walks the four steps", () => {
    expect(html).toContain("1. Start the stack");
    expect(html).toContain("2. Log in (passwordless)");
    expect(html).toContain("3. Seed a run and read it");
    expect(html).toContain("4. Do it for real on your repo");
  });

  it("shows the readiness check, not just the health check", () => {
    expect(html).toContain("http://127.0.0.1:8001/ready");
    expect(html).toContain("migrations");
  });

  it("labels the recorded demo as a replay", () => {
    expect(html).toContain("recorded replay");
    expect(html).toContain('href="/demo"');
  });

  it("links the pricing page and the migration command", () => {
    expect(html).toContain('href="/pricing"');
    expect(html).toContain("migrate_agent_config.py");
  });
});

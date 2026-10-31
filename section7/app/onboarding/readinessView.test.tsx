// W23 — first-run readiness card. Same protocol as checklistView.test.tsx:
// react-dom/server's real renderer, assertions on emitted markup.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReadinessReport } from "@/lib/api";
import { ReadinessCard } from "./readinessView";

const STALE: ReadinessReport = {
  ok: false,
  checks: [
    { check: "provider", status: "ok", component: "provider", reason: "CONFIGURED", detail: "openrouter.ai" },
    {
      check: "worker",
      status: "fail",
      component: "worker-daemon",
      reason: "WORKER_UNAUTHORIZED",
      detail: "the daemon rejected the orchestrator's token",
    },
    { check: "revision", status: "unknown", component: "worker-daemon", reason: "NOT_CHECKED", detail: "the daemon did not answer" },
  ],
};

describe("ReadinessCard", () => {
  it("names the failing component and says the project is not ready", () => {
    const html = renderToStaticMarkup(<ReadinessCard projectId="p1" report={STALE} error={null} />);
    expect(html).toContain('data-testid="readiness-check-worker"');
    expect(html).toContain("worker-daemon");
    expect(html).toContain("rejected the orchestrator");
    expect(html).toContain("Not ready");
    // unknown is shown as unknown, never as a pass
    const revision = html.split('data-testid="readiness-check-revision"')[1] ?? "";
    expect(revision).toContain("Unknown");
  });

  it("says ready only when the server says every check passed", () => {
    const ok: ReadinessReport = { ok: true, checks: [STALE.checks[0]] };
    const html = renderToStaticMarkup(<ReadinessCard projectId="p1" report={ok} error={null} />);
    expect(html).toContain("Ready to run");
  });

  it("asks for a project instead of guessing one", () => {
    const html = renderToStaticMarkup(<ReadinessCard projectId={null} report={null} error={null} />);
    expect(html).toContain('data-testid="readiness-no-project"');
  });

  it("shows a failed read as an error, not as a verdict", () => {
    const html = renderToStaticMarkup(<ReadinessCard projectId="p1" report={null} error="Network down" />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("Network down");
    expect(html).not.toContain("Ready to run");
    expect(html).not.toContain("Not ready");
  });
});

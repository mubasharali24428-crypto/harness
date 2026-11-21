/**
 * Dedicated UI week — before/after screenshot evidence (FUTURE PLAN 2.0 §5A.4).
 *
 * Captures the three core surfaces at 375, 768 and 1280 px in dark and light:
 *   home  — the inbox + working lists
 *   run   — the recorded demo replay with the scrubbable timeline
 *   review — the verdict card, using one stubbed review response because the
 *            synthetic demo PR needs a GitHub connection in Section 8
 *
 * UI_PHASE=before (default) writes before-*; run with UI_PHASE=after later in
 * the week. Nothing here starts or approves a run, and nothing calls a model.
 */
import { expect, test, type Page } from "@playwright/test";
import { journeyEmail, loginViaUi } from "./support";

const PHASE = process.env.UI_PHASE === "after" ? "after" : "before";
const SHOTS = "../docs/briefs/evidence/ui";
const WIDTHS = [375, 768, 1280] as const;
const THEMES = ["dark", "light"] as const;

async function applyTheme(page: Page, theme: (typeof THEMES)[number]) {
  await page.evaluate((next) => {
    window.localStorage.setItem("harness.theme", next);
    document.documentElement.dataset.theme = next;
  }, theme);
}

async function capture(page: Page, surface: string) {
  for (const theme of THEMES) {
    await applyTheme(page, theme);
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(120);
      await page.screenshot({
        path: `${SHOTS}/${PHASE}-${surface}-${width}-${theme}.png`,
        fullPage: true,
      });
    }
  }
}

test("UI week screenshots: home, run and review at three widths and both themes", async ({ page }) => {
  test.setTimeout(180_000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await loginViaUi(page, journeyEmail());

  // One proposed plan gives the inbox a real needs-you card in the evidence.
  // This is the same plan-gate call home.e2e.ts makes; nothing is started or
  // approved and no model is called.
  const orchestrator = process.env.E2E_ORCHESTRATOR_URL ?? "http://localhost:18004";
  const proposed = await page.evaluate(async (orch) => {
    const session = JSON.parse(window.localStorage.getItem("harness.session") ?? "{}");
    const projectId = window.localStorage.getItem("harness.projectId");
    const res = await fetch(`${orch}/api/v1/orchestrator/plans`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${session.token}` },
      body: JSON.stringify({ projectId, goal: "UI baseline: rename a constant" }),
    });
    return res.status;
  }, orchestrator);
  expect(proposed, "baseline plan proposal").toBe(201);

  await page.goto("/");
  await expect(page.getByTestId("composer")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await capture(page, "home");

  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?]+/, { timeout: 60_000 });
  await page.waitForLoadState("networkidle");
  // The demo lands on the Diff pane; the timeline is in the Evidence pane.
  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  await expect(page.getByTestId("run-timeline")).toBeVisible({ timeout: 30_000 });
  await capture(page, "run");

  await page.route("**/api/v1/reviews/phone-fixture", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        prId: "phone-fixture",
        prUrl: "https://example.test/pull/1",
        reviewStatus: "pending",
        approvedBy: null,
        changedFiles: [{ path: "auth/session.ts", additions: 4, deletions: 1 }],
        riskFlags: [
          { path: "auth/session.ts", severity: "high", rule: "auth-touch", reason: "touches auth" },
        ],
        testResults: [
          { name: "npm test", status: "pass", pass_count: 3, fail_count: 0, summary: "" },
        ],
        summary: { sections: [{ label: "Summary", text: "Screenshot fixture." }] },
      }),
    }),
  );
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/review?pr=phone-fixture");
  await expect(page.getByTestId("verdict-card")).toBeVisible({ timeout: 30_000 });
  await capture(page, "review");
});

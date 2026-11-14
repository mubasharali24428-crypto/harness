/**
 * R1 preview journey (docs/briefs/ui-master-prompt §SECTION R1).
 *
 * Walks the three preview routes against the REAL stack (compose RC frontend):
 *   /preview/run    — sidebar run list + run column of step blocks
 *   /preview/diff   — run column + right Diff pane (real PR diff)
 *   /preview/review — verdict card above the diff in the pane
 *
 * Uses the same sign-in + demo-seed flow as the a11y walk: a fresh team, the
 * demo seed gives a project, run and PR. Asserts real data on screen, the
 * four designed states reachable through real conditions (signed-out visit,
 * a run id that does not exist), zero console errors, axe clean at 1440, and
 * no horizontal scroll at 375.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { journeyEmail, loginViaUi } from "./support";

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

async function seedDemoRun(page: Page): Promise<string> {
  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?#]+/, { timeout: 60_000 });
  const stored = await page.evaluate(() => window.localStorage.getItem("harness.session"));
  expect(stored, "session exists after demo seed").toBeTruthy();
  const runId = await page.evaluate(() => window.localStorage.getItem("harness.runId"));
  expect(runId, "demo seed remembered the run id").toBeTruthy();
  return runId as string;
}

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map((v) => {
    const targets = v.nodes.slice(0, 3).map((n) => `${n.target.join(" ")} ${n.html.slice(0, 80)}`).join("; ");
    return `${v.id} (${v.impact ?? "n/a"}) x${v.nodes.length}: ${v.help} [${targets}]`;
  });
}

test.describe("R1 preview workspace", () => {
  let runId: string;

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    await loginViaUi(page, `r1-preview-${Date.now().toString(36)}@harness.dev`);
    runId = await seedDemoRun(page);
    await page.close();
  });

  test("signed-out visit shows the sign-in state, not a fake screen", async ({ page }) => {
    await page.goto("/preview/run");
    await page.evaluate(() => window.localStorage.removeItem("harness.session"));
    await page.goto("/preview/run");
    await expect(page.getByText("Sign in to see your runs")).toBeVisible();
  });

  test("/preview/run renders the demo run from real data with no console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    page.on("pageerror", (err) => errors.push(String(err)));

    await page.goto(`/preview/run?run=${encodeURIComponent(runId)}`);
    await expect(page.getByTestId("preview-workspace")).toBeVisible();
    // Sidebar: three groups, the demo run sits in Needs you (pr_opened).
    await expect(page.getByRole("heading", { name: "Needs you" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Working" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Done" })).toBeVisible();
    // Centre: goal title (not a run id), step blocks from the aggregate, plan, verdict.
    const title = page.locator(".ws-run-title");
    await expect(title).toBeVisible();
    await expect(title).not.toContainText("demo-run-");
    await expect(page.locator(".ws-steps .ws-step").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Plan", level: 2 })).toBeVisible();
    await expect(page.getByTestId("verdict-card")).toBeVisible();
    // Money rule: never a raw scale-6 number.
    await expect(page.locator(".ws-run-meta")).not.toContainText("$0.000057");
    expect(errors, "console errors on /preview/run").toEqual([]);
  });

  test("/preview/diff shows the real PR diff in the right pane", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.goto(`/preview/diff?run=${encodeURIComponent(runId)}`);
    await expect(page.getByTestId("preview-diff")).toBeVisible();
    await expect(page.locator(".ws-diff-file-head code").first()).toContainText("src/textstats/stats.py");
    await expect(page.locator(".ws-diff-line.add").first()).toBeVisible();
    await expect(page.locator(".ws-diff-line.del").first()).toBeVisible();
    expect(errors, "console errors on /preview/diff").toEqual([]);
  });

  test("/preview/review puts the verdict card above the diff", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.goto(`/preview/review?run=${encodeURIComponent(runId)}`);
    await expect(page.getByTestId("preview-review")).toBeVisible();
    const verdictBox = page.locator(".ws-verdict").first();
    const diffBox = page.getByTestId("preview-diff");
    await expect(verdictBox).toBeVisible();
    await expect(diffBox).toBeVisible();
    const v = await verdictBox.boundingBox();
    const d = await diffBox.boundingBox();
    expect(v && d && v.y < d.y, "verdict above diff").toBe(true);
    // Unknown facts stay words: "not recorded", never fabricated.
    await expect(page.getByTestId("preview-verdict-sandbox")).toContainText("not recorded");
    expect(errors, "console errors on /preview/review").toEqual([]);
  });

  test("a run id that does not exist renders the not-found state", async ({ page }) => {
    await page.goto("/preview/run?run=does-not-exist-xyz");
    await expect(page.getByText("Run not found")).toBeVisible();
  });

  test("no horizontal scroll at 375 on any of the three routes", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    for (const route of ["run", "diff", "review"]) {
      await page.goto(`/preview/${route}?run=${encodeURIComponent(runId)}`);
      await expect(page.getByTestId("preview-workspace")).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `horizontal overflow on /preview/${route}`).toBeLessThanOrEqual(0);
    }
  });

  test("axe clean at 1440 on all three routes", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const failures: Record<string, string[]> = {};
    for (const route of ["run", "diff", "review"]) {
      await page.goto(`/preview/${route}?run=${encodeURIComponent(runId)}`);
      await expect(page.getByTestId("preview-workspace")).toBeVisible();
      const found = await axeViolations(page);
      if (found.length > 0) failures[`/preview/${route}`] = found;
    }
    expect(failures, "preview routes have accessibility violations").toEqual({});
  });
});

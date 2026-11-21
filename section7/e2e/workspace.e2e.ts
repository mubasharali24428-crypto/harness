/**
 * R2 workspace journey (docs/briefs/ui-master-prompt, phase R2).
 *
 * Against the real stack: a fresh team seeds the demo run, then the run page is
 * walked as the three-pane workspace. Every former tab's content is one click
 * away, the pane survives reload and back, old ?tab= and /preview links still
 * land, the run view has at most three inputs, axe is clean and nothing
 * scrolls sideways at 375.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { loginViaUi } from "./support";

const PANES = ["Diff", "Plan", "Terminal", "Evidence", "Advanced"];

test.describe("R2 workspace", () => {
  let runPath: string;
  let session: string;

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    await loginViaUi(page, `r2-workspace-${Date.now().toString(36)}@harness.dev`);
    await page.goto("/demo");
    await page.getByTestId("demo-seed-button").click();
    await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?#]+/, { timeout: 60_000 });
    runPath = new URL(page.url()).pathname;
    session = (await page.evaluate(() => window.localStorage.getItem("harness.session"))) as string;
    await page.close();
  });

  async function open(page: Page, url: string) {
    await page.addInitScript((value) => window.localStorage.setItem("harness.session", value), session);
    await page.goto(url);
  }

  test("the run is titled by its goal, listed in the sidebar, with at most three inputs", async ({ page }) => {
    await open(page, runPath);
    await expect(page.locator(".ws-column")).toBeVisible();
    await expect(page.locator(".ws-run-title")).not.toHaveText(/^[0-9a-f-]{20,}$/i);
    await expect(page.locator(".ws-row.active")).toHaveCount(1);
    expect(await page.locator("input:visible, select:visible, textarea:visible").count()).toBeLessThanOrEqual(3);
  });

  test("every pane opens in one click and survives reload and back", async ({ page }) => {
    await open(page, runPath);
    for (const pane of PANES) {
      await page.getByRole("button", { name: pane, exact: true }).click();
      await expect(page.locator(".ws-pane-title")).toHaveText(pane);
      await expect(page).toHaveURL(new RegExp(`pane=${pane.toLowerCase()}`));
    }
    await page.reload();
    await expect(page.locator(".ws-pane-title")).toHaveText("Advanced");
    await page.goBack();
    await expect(page.locator(".ws-pane-title")).toHaveText("Evidence");
    await page.getByRole("button", { name: "Close the pane" }).click();
    await expect(page.locator("aside.ws-pane")).toBeHidden();
  });

  test("old links still land: ?tab=, the demo landing and /preview", async ({ page }) => {
    await open(page, `${runPath}?tab=changes&demo=1`);
    await expect(page.getByTestId("demo-replay-banner")).toBeVisible();
    await expect(page.getByTestId("preview-diff")).toBeVisible();
    await expect(page.getByTestId("preview-review")).toBeVisible();
    await page.goto(`${runPath}?tab=plan`);
    await expect(page.locator(".ws-pane-title")).toHaveText("Plan");
    await page.goto("/preview/run");
    await expect(page).toHaveURL(/\/$/);
  });

  test("a run id that does not exist says so", async ({ page }) => {
    await open(page, runPath.replace(/[^/]+$/, "does-not-exist-xyz"));
    await expect(page.getByText("Run not found")).toBeVisible();
  });

  test("axe clean at 1440 and no sideways scroll at 375, with each pane open", async ({ page }) => {
    await open(page, runPath);
    for (const pane of ["", ...PANES]) {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(pane ? `${runPath}?pane=${pane.toLowerCase()}` : runPath);
      await expect(page.locator(".ws-run-title")).toBeVisible();
      const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
      expect(axe.violations.map((v) => v.id), `axe on ${pane || "run"}`).toEqual([]);
      await page.setViewportSize({ width: 375, height: 812 });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `sideways scroll on ${pane || "run"}`).toBeLessThanOrEqual(0);
    }
  });
});

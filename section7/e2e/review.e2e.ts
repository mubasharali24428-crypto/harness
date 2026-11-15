/**
 * R4 + R5 on the real stack, using the seeded demo run only (no model calls):
 * comment on a file, approve from the keyboard, read the trace, export the run.
 * The risky-change gate is unit-tested (tests/unit/reviewFlow.test.ts) and
 * walked with an injected flag by scripts/r4-check.mjs; the demo PR has none.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { loginViaUi } from "./support";

const TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

test.describe("R4 review and R5 evidence", () => {
  let runPath: string;
  let session: string;

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    await loginViaUi(page, `r4-review-${Date.now().toString(36)}@harness.dev`);
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

  test("a reviewer comments on a file and approves from the keyboard", async ({ page }) => {
    await open(page, `${runPath}?pane=diff`);
    await expect(page.getByTestId("review-decide")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("review-request-changes")).toBeDisabled();

    const form = page.locator(".ws-diff-comment").first();
    await form.locator("summary").click();
    await form.locator("input[type=number]").fill("3");
    await form.locator("textarea").fill("name this constant");
    await form.getByRole("button", { name: "Add comment" }).click();
    await expect(page.getByTestId("review-comments")).toContainText("name this constant");
    await expect(page.locator(".ws-decide [role=status]").last()).toContainText("not sent to the agent");

    await page.getByTestId("review-approve").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Approved. Merging still happens on GitHub")).toBeVisible();
    expect((await new AxeBuilder({ page }).withTags(TAGS).analyze()).violations).toEqual([]);
  });

  test("the evidence pane shows the cost, a trace, and exports the run history", async ({ page }) => {
    await open(page, `${runPath}?pane=evidence`);
    await expect(page.getByTestId("evidence-cost")).toBeVisible({ timeout: 30_000 });
    expect(await page.locator(".ws-trace li").count()).toBeGreaterThan(0);
    expect((await new AxeBuilder({ page }).withTags(TAGS).analyze()).violations).toEqual([]);

    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 40_000 }),
      page.getByTestId("evidence-export").click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^harness-run-.+\.json$/);
  });
});

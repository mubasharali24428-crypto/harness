/**
 * Part 56 — accessibility walk. axe-core checks every routed page a signed-in
 * user opens (WCAG 2.2 A/AA, including target size and colour contrast), and
 * the run page when the stack has a run. Same stack, login and skip rules as
 * the journey suite (global-setup.ts).
 *
 * Assertion law: a page passes only with zero axe violations. The walk does
 * not sample: every route listed in PAGES is checked on every run.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { journeyEmail, loginViaUi } from "./support";

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];
const PAGES = ["/", "/runs", "/notifications", "/settings", "/dashboard/cost"];

/** axe violations as "rule (impact) xN: help [first targets]" lines. */
async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map((v) => {
    const targets = v.nodes
      .slice(0, 3)
      .map((n) => `${n.target.join(" ")} ${n.html.slice(0, 80)}`)
      .join("; ");
    return `${v.id} (${v.impact ?? "n/a"}) x${v.nodes.length}: ${v.help} [${targets}]`;
  });
}

test.describe("Part 56 accessibility walk (compose stack)", () => {
  test("login page has no WCAG 2.2 A/AA violations", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "HARNESS" })).toBeVisible();
    expect(await axeViolations(page), "/login has accessibility violations").toEqual([]);
  });

  test("signed-in pages have no WCAG 2.2 A/AA violations", async ({ page }) => {
    test.setTimeout(180_000);
    await loginViaUi(page, journeyEmail());
    // Every page is scanned even after one fails, so a single run lists all gaps.
    const failures: Record<string, string[]> = {};
    for (const path of PAGES) {
      const response = await page.goto(path);
      expect(response?.status(), `${path} status`).toBeLessThan(400);
      await page.waitForLoadState("networkidle");
      // AppShell bounces to /login without a session; scanning that page would pass for the wrong reason.
      expect(new URL(page.url()).pathname, `${path} redirected away`).toBe(path);
      const found = await axeViolations(page);
      if (found.length > 0) failures[path] = found;
    }
    expect(failures, "signed-in pages have accessibility violations").toEqual({});
  });

  test("run page has no WCAG 2.2 A/AA violations", async ({ page }) => {
    test.setTimeout(120_000);
    // A fresh user has no runs; "Try the demo" seeds a replayed run for the signed-in team.
    await loginViaUi(page, journeyEmail());
    await page.goto("/demo");
    await page.getByTestId("demo-seed-button").click();
    await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?]+/, { timeout: 60_000 });
    await page.waitForLoadState("networkidle");
    expect(await axeViolations(page), "run page has accessibility violations").toEqual([]);
  });
});

/**
 * Part 56 — accessibility walk. axe-core checks every routed page a signed-in
 * user opens (WCAG 2.2 A/AA, including target size and colour contrast), and
 * the run-page family when the stack has a run. Same stack, login and skip
 * rules as the journey suite (global-setup.ts).
 *
 * Assertion law: a page passes only with zero axe violations. The walk does
 * not sample: every route listed in PAGES is checked on every run.
 *
 * F2 — PAGES now carries every static route from the U0 inventory
 * (docs/briefs/evidence/ui/u0-inventory-2026-11-14.md, rev f7af86d); /login is
 * covered pre-auth by the login test, and the run test walks the inventory's
 * three dynamic routes resolved from the demo-seeded run.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { journeyEmail, loginViaUi } from "./support";

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

/**
 * Every static route in the inventory (app/<path>/page.tsx → URL), in
 * inventory order, minus /login — the login test below checks it pre-auth,
 * which is how a fresh user actually meets it. app/layout.tsx is not a
 * navigable route (it renders inside every page) and is not listed here.
 */
const PAGES = [
  "/",
  "/auth/sso/callback",
  "/dashboard/cost",
  "/demo",
  "/docs",
  "/meeting",
  "/notifications",
  "/notifications/unsubscribe",
  "/onboarding",
  "/pricing",
  "/review",
  "/runs",
  "/settings",
  "/settings/account",
  "/settings/billing",
  "/settings/governance",
  "/settings/integrations",
  "/settings/keys",
  "/settings/models",
  "/settings/notifications",
  "/settings/project",
  "/settings/team",
] as const;

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
    test.setTimeout(600_000); // 22 routes × goto + axe scan; one run must list every gap
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
    test.setTimeout(300_000); // demo seed + three dynamic run routes
    // A fresh user has no runs; "Try the demo" seeds a replayed run for the signed-in team.
    await loginViaUi(page, journeyEmail());
    await page.goto("/demo");
    await page.getByTestId("demo-seed-button").click();
    await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?]+/, { timeout: 60_000 });
    await page.waitForLoadState("networkidle");
    // F2: the inventory's dynamic routes, resolved from the seeded run —
    // /projects/[projectId]/runs/[runId], its /checkpoints child, /projects/[projectId]/tree.
    const segments = new URL(page.url()).pathname.split("/").filter(Boolean); // projects <id> runs <runId>
    expect(segments[0], "demo seed landed on a project run page").toBe("projects");
    const runPath = `/projects/${segments[1]}/runs/${segments[3]}`;
    const runPages = [runPath, `${runPath}/checkpoints`, `/projects/${segments[1]}/tree`];
    // Every page is scanned even after one fails, so a single run lists all gaps.
    const failures: Record<string, string[]> = {};
    for (const path of runPages) {
      const response = await page.goto(path);
      expect(response?.status(), `${path} status`).toBeLessThan(400);
      await page.waitForLoadState("networkidle");
      expect(new URL(page.url()).pathname, `${path} redirected away`).toBe(path);
      const found = await axeViolations(page);
      if (found.length > 0) failures[path] = found;
    }
    expect(failures, "run pages have accessibility violations").toEqual({});
  });
});

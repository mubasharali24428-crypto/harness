/**
 * U0 — command palette journey (docs/plans/ui-overhaul-plan-2026-11-13.md).
 * Keyboard only: open with Ctrl/Cmd+K, filter, open a section, jump with "g then letter".
 * Also: axe on the open palette, and the 100 ms budget from key press to a visible palette.
 *
 * F2 — the 100 ms open budget is now measured on EVERY route from the U0
 * inventory (docs/briefs/evidence/ui/u0-inventory-2026-11-14.md, rev f7af86d):
 * the keyboard journey stays on /settings, then the same budget runs on every
 * static route, then on the three dynamic run routes resolved from a
 * demo-seeded run. Every route is measured even after one breaches, so a
 * single run lists all gaps.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { journeyEmail, loginViaUi } from "./support";

const OPEN_BUDGET_MS = 100;

/** Every static route in the inventory (app/<path>/page.tsx → URL), in inventory order. */
const ROUTES = [
  "/",
  "/auth/sso/callback",
  "/dashboard/cost",
  "/demo",
  "/docs",
  "/login",
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

/**
 * Budget measured wholly in the page, from the key event to the input entering
 * the DOM, so test-runner latency is not counted. Same mechanics as the
 * original /settings measurement, reused per route.
 */
async function openPaletteMs(page: Page, input: Locator): Promise<number> {
  await page.evaluate(() => {
    const w = window as unknown as { __t0: number; __t1: number };
    w.__t0 = w.__t1 = 0;
    window.addEventListener(
      "keydown",
      (e) => {
        // not `once`: the Control key press arrives first and would use the listener up
        if (e.key.toLowerCase() === "k" && !w.__t0) w.__t0 = performance.now();
      },
      { capture: true },
    );
    const seen = new MutationObserver(() => {
      if (document.querySelector("[cmdk-input]")) {
        w.__t1 = performance.now();
        seen.disconnect();
      }
    });
    seen.observe(document.body, { childList: true, subtree: true });
  });
  await page.keyboard.press("Control+k");
  await expect(input).toBeVisible();
  return page.evaluate(() => {
    const w = window as unknown as { __t0: number; __t1: number };
    return w.__t1 - w.__t0;
  });
}

/** One budget measurement per route: goto, no-redirect guard, measure, record, close. */
async function budgetOnRoute(
  page: Page,
  input: Locator,
  route: string,
  breaches: Record<string, string>,
): Promise<void> {
  await page.goto(route);
  await page.waitForLoadState("networkidle");
  // A route that bounces away would have its budget attributed to the wrong page.
  expect(new URL(page.url()).pathname, `${route} redirected away`).toBe(route);
  const ms = await openPaletteMs(page, input).catch((err: unknown) => {
    breaches[route] = `palette did not open: ${String(err).split("\n")[0]}`;
    return null;
  });
  if (ms === null) return;
  test.info().annotations.push({ type: "palette-open-ms", description: `${route} ${ms.toFixed(1)}` });
  console.log(`[palette] ${route} open ${ms.toFixed(1)} ms (budget ${OPEN_BUDGET_MS})`);
  if (!(ms > 0) || ms >= OPEN_BUDGET_MS) breaches[route] = `${ms.toFixed(1)} ms`;
  await page.keyboard.press("Escape");
  await expect(input).toBeHidden();
}

test("command palette: keyboard journey, axe clean, opens within budget", async ({ page }) => {
  test.setTimeout(600_000); // one signed-in session: journey + budget on every inventory route
  await loginViaUi(page, journeyEmail());

  // Journey phase (unchanged law), measured from /settings.
  await page.goto("/settings");
  await page.waitForLoadState("networkidle");
  const input = page.getByPlaceholder("Where to?");
  const elapsed = await openPaletteMs(page, input);
  expect(elapsed, `palette took ${elapsed.toFixed(1)} ms`).toBeGreaterThan(0);
  expect(elapsed, `palette took ${elapsed.toFixed(1)} ms`).toBeLessThan(OPEN_BUDGET_MS);
  test.info().annotations.push({ type: "palette-open-ms", description: `/settings ${elapsed.toFixed(1)}` });
  console.log(`[palette] /settings open ${elapsed.toFixed(1)} ms (budget ${OPEN_BUDGET_MS})`);

  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`), "open palette has accessibility violations").toEqual([]);

  await input.fill("spend");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/dashboard\/cost$/);
  await expect(input).toBeHidden();

  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("g");
  await page.keyboard.press("r");
  await expect(page).toHaveURL(/\/runs$/);

  // Escape closes without navigating.
  await page.keyboard.press("Control+k");
  await expect(input).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(input).toBeHidden();
  await expect(page).toHaveURL(/\/runs$/);

  // F2: the same 100 ms budget on every inventory route. Failures collect in
  // `breaches` so one run lists every gap instead of stopping at the first.
  const breaches: Record<string, string> = {};
  for (const route of ROUTES) {
    await budgetOnRoute(page, input, route, breaches);
  }

  // The inventory's three dynamic routes need real ids; seed a run the way the
  // a11y suite does ("Try the demo" replays a run for the signed-in team).
  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?]+/, { timeout: 60_000 });
  await page.waitForLoadState("networkidle");
  const segments = new URL(page.url()).pathname.split("/").filter(Boolean); // projects <id> runs <runId>
  expect(segments[0], "demo seed landed on a project run page").toBe("projects");
  const runPath = `/projects/${segments[1]}/runs/${segments[3]}`;
  for (const route of [runPath, `${runPath}/checkpoints`, `/projects/${segments[1]}/tree`]) {
    await budgetOnRoute(page, input, route, breaches);
  }

  expect(breaches, `palette open budget (${OPEN_BUDGET_MS} ms) breached on some routes`).toEqual({});
});

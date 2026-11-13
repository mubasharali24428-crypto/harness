/**
 * U0 — command palette journey (docs/plans/ui-overhaul-plan-2026-11-13.md).
 * Keyboard only: open with Ctrl/Cmd+K, filter, open a section, jump with "g then letter".
 * Also: axe on the open palette, and the 100 ms budget from key press to a visible palette.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { journeyEmail, loginViaUi } from "./support";

const OPEN_BUDGET_MS = 100;

test("command palette: keyboard journey, axe clean, opens within budget", async ({ page }) => {
  await loginViaUi(page, journeyEmail());
  await page.goto("/settings");
  await page.waitForLoadState("networkidle");
  const input = page.getByPlaceholder("Where to?");

  // Budget: measured wholly in the page, from the key event to the input entering the DOM,
  // so test-runner latency is not counted.
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
  const elapsed = await page.evaluate(() => {
    const w = window as unknown as { __t0: number; __t1: number };
    return w.__t1 - w.__t0;
  });
  expect(elapsed, `palette took ${elapsed.toFixed(1)} ms`).toBeGreaterThan(0);
  expect(elapsed, `palette took ${elapsed.toFixed(1)} ms`).toBeLessThan(OPEN_BUDGET_MS);
  test.info().annotations.push({ type: "palette-open-ms", description: elapsed.toFixed(1) });
  console.log(`[palette] open ${elapsed.toFixed(1)} ms (budget ${OPEN_BUDGET_MS})`);

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
});

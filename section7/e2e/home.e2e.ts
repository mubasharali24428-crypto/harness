/**
 * U1 — home inbox (docs/plans/ui-overhaul-plan-2026-11-13.md §5).
 * Done-when: a reviewer finds and opens the oldest pending item in under 10 s, keyboard only.
 * The counts on screen must equal what the orchestrator reports for the team.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { journeyEmail, loginViaUi } from "./support";

const NEEDS_YOU = new Set(["awaiting_plan_approval", "blocked", "pr_opened"]);
const WORKING = new Set(["planning", "executing", "paused", "awaiting_done_signal", "reviewing"]);

test("home: needs-you and working match the API; oldest item opens by keyboard in < 10 s", async ({ page }) => {
  await loginViaUi(page, journeyEmail());
  const orchestrator = process.env.E2E_ORCHESTRATOR_URL ?? "http://localhost:8004";

  // Leave one run waiting for plan approval, through the same API the app uses.
  const planId = await page.evaluate(async (orch) => {
    const s = JSON.parse(window.localStorage.getItem("harness.session") ?? "{}");
    const pid = window.localStorage.getItem("harness.projectId");
    const res = await fetch(`${orch}/api/v1/orchestrator/plans`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${s.token}` },
      body: JSON.stringify({ projectId: pid, goal: "U1 home check: rename a constant" }),
    });
    const body = await res.json().catch(() => ({}));
    return res.status === 201 ? (body.planId ?? null) : `status ${res.status}`;
  }, orchestrator);
  test.info().annotations.push({ type: "proposed-plan", description: String(planId) });
  console.log(`[home] proposed plan: ${String(planId)}`);

  await page.goto("/");
  const needs = page.getByTestId("home-needs-you");
  const working = page.getByTestId("home-working");
  await expect(needs.getByRole("heading", { name: /Needs you/ })).toBeVisible();
  await expect(working.getByRole("heading", { name: /Working now/ })).toBeVisible();
  await expect(needs.getByText("Loading…")).toHaveCount(0);

  const api = await page.evaluate(async (orch) => {
    const s = JSON.parse(window.localStorage.getItem("harness.session") ?? "{}");
    const res = await fetch(`${orch}/api/v1/orchestrator/runs?limit=100`, {
      headers: { authorization: `Bearer ${s.token}` },
    });
    return ((await res.json()).items ?? []) as { status: string }[];
  }, orchestrator);
  const expectNeeds = api.filter((r) => NEEDS_YOU.has(r.status)).length;
  const expectWorking = api.filter((r) => WORKING.has(r.status)).length;
  await expect(needs.locator("h2 span")).toHaveText(String(expectNeeds));
  await expect(working.locator("h2 span")).toHaveText(String(expectWorking));
  if (expectNeeds > 0) await expect(page).toHaveTitle(new RegExp(`^\\(${expectNeeds}\\) `));

  // The page entrance fades content in for 420 ms; scanning mid-fade measures blended colours.
  await page.waitForFunction(() => document
      .getAnimations()
      // the brand and live-dot pulses loop forever; only one-shot entrances matter here
      .every((a) => a.playState !== "running" || a.effect?.getTiming().iterations === Infinity));
  const axe = await new AxeBuilder({ page })
    .include("[data-testid=home-needs-you]")
    .include("[data-testid=home-working]")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(
    axe.violations.map((v) => `${v.id}: ${v.help} [${v.nodes.map((n) => `${n.html.slice(0, 90)} :: ${n.failureSummary ?? ""}`).join(" | ")}]`),
  ).toEqual([]);

  test.skip(expectNeeds === 0, "nothing is waiting on a person on this stack; the keyboard leg needs one item");
  // Keyboard only, timed: Tab from the top of the page until the first needs-you link has focus.
  const first = needs.getByRole("link").first();
  const href = await first.getAttribute("href");
  const t0 = Date.now();
  await page.locator("body").press("Home");
  for (let i = 0; i < 40; i += 1) {
    await page.keyboard.press("Tab");
    if (await first.evaluate((el) => el === document.activeElement)) break;
  }
  await expect(first).toBeFocused();
  await page.keyboard.press("Enter");
  // the link may carry ?tab=plan: compare the path and query literally, not as a pattern
  await expect.poll(() => new URL(page.url()).pathname + new URL(page.url()).search).toBe(href);
  const seconds = (Date.now() - t0) / 1000;
  console.log(`[home] oldest pending item opened by keyboard in ${seconds.toFixed(2)} s`);
  expect(seconds).toBeLessThan(10);
});

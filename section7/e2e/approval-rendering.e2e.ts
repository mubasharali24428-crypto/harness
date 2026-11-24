/**
 * Sprint 2 / Task 11 — X-07 Plain + X-21 Decision card in a real browser.
 *
 * Runs WITHOUT the compose stack: every /api/v1 call is answered by page.route with one
 * fixed approval (the same shape section4 collab.list_approvals returns), so this proves the
 * UI half — rendering choice, keyboard-only decision, axe, dominant band above the fold at
 * 1280 and 375 px. The backend half is section4 test_collab_backend / test_approval_copy.
 *
 *   E2E_SKIP_STACK_CHECK=1 E2E_BASE_URL=http://localhost:3020 \
 *     npx playwright test e2e/approval-rendering.e2e.ts
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { APPROVAL } from "./approvalFixture";

const SHOTS = "../docs/briefs/evidence/sprint2";
const RUN = "/projects/p1/runs/r1?pane=advanced";

async function stub(page: Page, rendering: string, decisions: unknown[]) {
  await page.addInitScript(() => {
    window.localStorage.setItem("harness.session", JSON.stringify({
      token: "e2e-token", principal: { userId: "lead", teamId: "team-a", role: "engineer" },
    }));
  });
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.pathname === "/api/v1/me/rendering") return json({ userId: "lead", rendering });
    if (url.pathname === "/api/v1/orchestrator/runs/r1") {
      return json({ runId: "r1", state: "done", planId: null, planApproved: false, isHumanEditing: false,
                    executing: false, engineMode: null, costUsd: null, estimatedCostUsd: null, prId: null,
                    url: null, steps: [], budget: { degraded: false }, lifecycle: null });
    }
    if (url.pathname.endsWith("/runs/r1/participants")) {
      return json({ participants: [{ userId: "lead", role: "leader" }, { userId: "eng2", role: "participant" }],
                    leader: "lead", pendingHandoff: null });
    }
    if (url.pathname.endsWith("/runs/r1/approvals")) return json({ approvals: [APPROVAL] });
    if (url.pathname.endsWith("/approvals/a1/decision")) {
      decisions.push(route.request().postDataJSON());
      return json({ ...APPROVAL, state: "approved", decidedBy: "lead" });
    }
    return json({ error: { code: "NOT_FOUND", message: "not stubbed in this spec" } }, 404);
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
}

async function openCard(page: Page) {
  await page.goto(RUN);
  const card = page.getByTestId("approval-a1");
  await expect(card).toBeVisible({ timeout: 60_000 });
  return card;
}

test("plain rendering: the sentence leads, technical record stays", async ({ page }) => {
  await stub(page, "plain", []);
  const card = await openCard(page);
  await expect(page.getByTestId("plain-a1")).toHaveText(APPROVAL.plain!);
  await expect(card.getByLabel("Proposed change")).toContainText("+NEW_KEY");
  await expect(page.getByTestId("grid-a1")).toHaveCount(0);
  await card.screenshot({ path: `${SHOTS}/x07-plain-1280.png` });
});

for (const viewport of [{ width: 1280, height: 800 }, { width: 375, height: 812 }]) {
  test(`decision card at ${viewport.width}px: dominant band above the fold and above Approve`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await stub(page, "decision", []);
    const card = await openCard(page);
    await card.evaluate((el) => el.scrollIntoView({ block: "start" }));
    const dominant = page.getByTestId("grid-a1-dominant");
    await expect(dominant).toContainText("critical");
    await expect(dominant).toBeInViewport();
    const d = (await dominant.boundingBox())!;
    const approve = (await card.getByRole("button", { name: "Approve this change" }).boundingBox())!;
    expect(d.y + d.height).toBeLessThanOrEqual(viewport.height);
    expect(d.y).toBeLessThan(approve.y);
    // Every active cell (the critical credentials one included) must sit inside the card, not clipped.
    const box = (await card.boundingBox())!;
    const cells = await page.getByTestId("grid-a1").getByTestId("grid-cell").all();
    expect(cells).toHaveLength(2);
    for (const cell of cells) {
      const c = (await cell.boundingBox())!;
      expect(c.x + c.width, "grid cell clipped by the card").toBeLessThanOrEqual(box.x + box.width);
    }
    await card.screenshot({ path: `${SHOTS}/x21-decision-${viewport.width}.png` });
  });
}

test("keyboard only: reach and press Approve without a mouse; digest echoed", async ({ page }) => {
  const decisions: unknown[] = [];
  await stub(page, "decision", decisions);
  const card = await openCard(page);
  await card.focus();
  const seen: string[] = [];
  for (let i = 0; i < 4; i += 1) {
    await page.keyboard.press("Tab");
    seen.push(await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return el?.getAttribute("aria-label") ?? el?.textContent?.trim() ?? "";
    }));
    if (seen.at(-1) === "Approve this change") break;
  }
  expect(seen).toContain("Approve this change");
  await page.keyboard.press("Enter");
  await expect.poll(() => decisions.length).toBe(1);
  expect(decisions[0]).toEqual({ approve: true, digest: APPROVAL.digest });
});

for (const rendering of ["plain", "decision"]) {
  test(`axe: ${rendering} approval card has no serious or critical violations`, async ({ page }) => {
    await stub(page, rendering, []);
    await openCard(page);
    const results = await new AxeBuilder({ page })
      .include('[data-testid="approval-a1"]')
      .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
      .analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });
}

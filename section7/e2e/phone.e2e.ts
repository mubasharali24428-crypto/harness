/**
 * T5 — phone width (375 × 812). The home lists, command palette, run timeline
 * and verdict card must be usable with no horizontal page scroll.
 *
 * The run comes from the canned demo (recorded replay), so this spec never
 * starts or approves a live run. Screenshots land in docs/briefs/evidence/ui/.
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { journeyEmail, loginViaUi } from "./support";

test.use({ viewport: { width: 375, height: 812 } });

const SHOTS = "../docs/briefs/evidence/ui";
const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

async function expectNoHorizontalScroll(page: Page, label: string) {
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width, `${label}: document scrollWidth must stay within 375 px`).toBeLessThanOrEqual(375);
}

test("phone 375: home, palette, run timeline and verdict card do not scroll sideways", async ({ page }) => {
  test.setTimeout(180_000);
  // The app fades content in (`rise`, up to 200 ms delay). axe samples computed
  // colours, so scanning mid-fade reports false contrast failures. Reduced
  // motion collapses those animations to 0.01 ms (globals.css Part 56/57).
  await page.emulateMedia({ reducedMotion: "reduce" });
  await loginViaUi(page, journeyEmail());

  // 1. Home — the new-run composer, no sideways scroll.
  await page.goto("/");
  await expect(page.getByTestId("composer")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expectNoHorizontalScroll(page, "home");
  await page.screenshot({ path: `${SHOTS}/phone-home-375.png`, fullPage: true });

  // 2. Command palette — opens at Cmd/Ctrl+K and fits the viewport.
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Command palette" })).toBeVisible();
  await expectNoHorizontalScroll(page, "palette");
  await page.screenshot({ path: `${SHOTS}/phone-palette-375.png` });
  await page.keyboard.press("Escape");

  // 3. Run page — seed the recorded demo, then check the scrubbable timeline.
  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?]+/, { timeout: 60_000 });
  await page.waitForLoadState("networkidle");
  // The demo lands on the Diff pane; the timeline is in the Evidence pane.
  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  await expect(page.getByTestId("run-timeline")).toBeVisible({ timeout: 30_000 });
  await expectNoHorizontalScroll(page, "run page");
  await page.screenshot({ path: `${SHOTS}/phone-run-375.png`, fullPage: true });

  // 4. Review — the demo PR is synthetic and section8 requires a GitHub
  //    connection to hydrate it, so this leg stubs ONE review response to
  //    verify the verdict card's real layout at 375 px. The card's data rules
  //    are unit-tested separately; nothing here approves or starts a run.
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
        summary: { sections: [{ label: "Summary", text: "Phone-layout fixture." }] },
      }),
    }),
  );
  await page.goto("/review?pr=phone-fixture");
  await expect(page.getByTestId("verdict-card")).toBeVisible({ timeout: 30_000 });
  await expectNoHorizontalScroll(page, "review page");
  const axe = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(
    axe.violations.map(
      (v) =>
        `${v.id}: ${v.help} :: ${v.nodes
          .map((n) => `${n.target.join(" ")} — ${n.failureSummary ?? ""}`)
          .join(" | ")}`,
    ),
    "review page has accessibility violations",
  ).toEqual([]);
  await page.screenshot({ path: `${SHOTS}/phone-review-375.png`, fullPage: true });
});

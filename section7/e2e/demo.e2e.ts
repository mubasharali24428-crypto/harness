/**
 * U5 slice — "Try the demo" entry (docs/plans/ui-overhaul-plan-2026-11-13.md §5).
 *
 * The RC loop was: signed-out /demo → /login?next=demo → login's "Try the demo"
 * → /demo (still signed out) → /login?next=demo … nothing ever seeded.
 * This spec walks the fixed flow end to end: the login round-trip returns to
 * /demo?start=1, which seeds the recorded replay and lands on the run page.
 *
 * The demo is a replay fixture, not a live run: the spec never approves or
 * starts anything, and asserts the honest replay banner on the landing page.
 */
import { expect, test } from "@playwright/test";
import { journeyEmail } from "./support";

test("demo: signed-out /demo reaches the replay view without looping", async ({ page }) => {
  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();

  // Signed out, the seed button asks for a key-free identity first.
  await expect(page).toHaveURL(/\/login\?next=demo$/);
  // The old second "Try the demo" button pushed back to /demo signed-out —
  // it must be gone in this flow, replaced by the one-action email form.
  await expect(page.getByRole("button", { name: /Try the demo/i })).toHaveCount(0);
  await expect(page.getByTestId("login-demo-note")).toBeVisible();

  await page.getByPlaceholder("you@team.dev").fill(journeyEmail());
  await page.getByRole("button", { name: /Enter the harness/i }).click();

  // Login stores the session, returns to /demo?start=1, and the page seeds
  // itself — no second demo click. Landing is the completed run + replay banner.
  await expect(page).toHaveURL(/\/projects\/[^/]+\/runs\/[^/?#]+\?tab=changes&demo=1$/, {
    timeout: 45_000,
  });
  await expect(page.getByTestId("demo-replay-banner")).toBeVisible();
  await expect(page.getByTestId("demo-replay-banner")).toContainText(/recorded session/i);

  // R0 (plan §8 F1/F2): two clicks in, the replay shows its own diff and a
  // populated verdict card, with no GitHub connection on this account.
  await expect(page.getByTestId("pr-diff")).toContainText("median_word_length", { timeout: 20_000 });
  await expect(page.getByTestId("pr-diff")).toContainText("+    lengths.sort()");
  await expect(page.getByTestId("verdict-card")).toBeVisible();
  await expect(page.getByTestId("verdict-plan")).not.toBeEmpty();
  await expect(page.getByTestId("verdict-cost")).toContainText("$");
  await expect(page.getByText(/github not connected/i)).toHaveCount(0);
  await expect(page.getByText("No snapshots yet")).toHaveCount(0);
});

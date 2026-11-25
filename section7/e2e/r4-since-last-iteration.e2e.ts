/**
 * R4 exit: the Diff pane opens on "Since last iteration" (the newest
 * checkpoint's hunks), toggles to the full pull request, keeps the decision
 * visible, never flashes the snapshot history while since-last is primary,
 * and re-defaults on a run switch.
 *
 * Same stack and seeded demo runs as review.e2e.ts (no model calls). The demo
 * runs have no sandbox snapshots, so the sandbox checkpoint routes are stubbed
 * here with one iteration; everything else (identity, runs, review service)
 * is the real stack. Revert is stubbed too, and the stub asserts the body is
 * exactly { hunks: [{ path, index }] }.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import { loginViaUi } from "./support";

const TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];
const SHA = "c0ffee1234567890c0ffee1234567890c0ffee12";
const REVERT_SHA = "beef00001234567890beef00001234567890beef";

const CHECKPOINTS = /\/api\/v1\/sandbox\/[^/]+\/checkpoints(\?.*)?$/;
const HUNKS = /\/api\/v1\/sandbox\/[^/]+\/checkpoints\/[^/]+\/hunks(\?.*)?$/;
const REVERT = /\/api\/v1\/sandbox\/[^/]+\/checkpoints\/[^/]+\/revert$/;

const HUNK_FILES = [
  {
    path: "src/greeting.ts",
    binary: false,
    hunks: [
      {
        index: 0,
        header: "@@ -1,3 +1,3 @@",
        lines: [" export function greet(name: string) {", "-  return `hi ${name}`;", "+  return `Hello, ${name}!`;", " }"],
        additions: 1,
        deletions: 1,
      },
    ],
  },
];

test.describe("R4 since last iteration", () => {
  let runPaths: string[] = [];
  let session: string;

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    await loginViaUi(page, `r4-since-last-${Date.now().toString(36)}@harness.dev`);
    for (let i = 0; i < 2; i += 1) {
      await page.goto("/demo");
      await page.getByTestId("demo-seed-button").click();
      await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?#]+/, { timeout: 60_000 });
      runPaths.push(new URL(page.url()).pathname);
    }
    runPaths = [...new Set(runPaths)];
    session = (await page.evaluate(() => window.localStorage.getItem("harness.session"))) as string;
    await page.close();
  });

  async function stubSnapshots(page: Page, revertBodies: unknown[] = []) {
    let newest = SHA;
    // The sandbox is another origin than the frontend, so a stubbed answer
    // needs CORS headers, and the preflight (the request carries Authorization)
    // must be answered too, or the browser drops the stub.
    const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" };
    const reply = (route: Route, body: () => unknown) =>
      route.request().method() === "OPTIONS"
        ? route.fulfill({ status: 204, headers: cors })
        : route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(body()) });

    await page.route(CHECKPOINTS, (route) =>
      reply(route, () => ({
        projectId: "demo",
        nextCursor: null,
        items: [{ sha: newest, message: "checkpoint: post-edit step-2", createdAt: new Date().toISOString(), taskId: "sandbox-task" }],
      })),
    );
    await page.route(HUNKS, (route) =>
      reply(route, () => ({ projectId: "demo", sha: newest, files: newest === SHA ? HUNK_FILES : [] })),
    );
    await page.route(REVERT, (route) => {
      if (route.request().method() !== "OPTIONS") {
        revertBodies.push(route.request().postDataJSON());
        newest = REVERT_SHA;
      }
      return reply(route, () => ({ status: "reverted", sha: REVERT_SHA, reverted: 1 }));
    });
  }

  async function open(page: Page, url: string) {
    await page.addInitScript((value) => {
      window.localStorage.setItem("harness.session", value);
      // Records whether the snapshot history ever attached, to prove there is no flash.
      (window as unknown as { __changeReviewSeen: boolean }).__changeReviewSeen = false;
      new MutationObserver(() => {
        if (document.querySelector('[data-testid="change-review"]')) {
          (window as unknown as { __changeReviewSeen: boolean }).__changeReviewSeen = true;
        }
      }).observe(document, { childList: true, subtree: true });
    }, session);
    await page.goto(url);
  }

  test("the Diff pane opens on the last iteration, with the decision and no snapshot history", async ({ page }) => {
    await stubSnapshots(page);
    await open(page, `${runPaths[0]}?pane=diff`);

    await expect(page.getByTestId("since-last-diff")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("diff-scope-since-last")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("since-last-summary")).toContainText("1 file · 1 hunk · +1 −1");
    await expect(page.getByTestId("since-last-source")).toContainText("not tagged with this run");
    await expect(page.getByTestId("review-decide")).toBeVisible();
    await expect(page.getByTestId("change-review")).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __changeReviewSeen: boolean }).__changeReviewSeen)).toBe(false);
    expect((await new AxeBuilder({ page }).include(".ws-pane").withTags(TAGS).analyze()).violations).toEqual([]);
  });

  test("the toggle switches to the full pull request and back", async ({ page }) => {
    await stubSnapshots(page);
    await open(page, `${runPaths[0]}?pane=diff`);
    await expect(page.getByTestId("since-last-diff")).toBeVisible({ timeout: 30_000 });

    await page.getByTestId("diff-scope-full-pr").click();
    await expect(page.getByTestId("diff-scope-full-pr")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("preview-diff")).toBeVisible();
    await expect(page.getByTestId("change-review")).toBeVisible();
    await expect(page.getByTestId("since-last-diff")).toHaveCount(0);
    await expect(page.getByText(/view is not available/)).toHaveCount(0);

    await page.getByTestId("diff-scope-since-last").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("since-last-diff")).toBeVisible();
    await expect(page.getByTestId("change-review")).toHaveCount(0);
  });

  test("a hunk revert sends only { path, index }", async ({ page }) => {
    const bodies: unknown[] = [];
    await stubSnapshots(page, bodies);
    await open(page, `${runPaths[0]}?pane=diff`);
    await expect(page.getByTestId("since-last-diff")).toBeVisible({ timeout: 30_000 });

    await expect(page.getByTestId("since-last-revert")).toBeDisabled();
    await page.getByLabel("Revert hunk 1 in src/greeting.ts").check();
    await page.getByTestId("since-last-revert").click();
    await expect(page.getByText(/Reverted 1 hunk\(s\)/)).toBeVisible();
    expect(bodies).toEqual([{ hunks: [{ path: "src/greeting.ts", index: 0 }] }]);
  });

  test("switching runs starts again on since-last", async ({ page }) => {
    test.skip(runPaths.length < 2, "the demo seeded one run only");
    await stubSnapshots(page);
    await open(page, `${runPaths[0]}?pane=diff`);
    await expect(page.getByTestId("since-last-diff")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("diff-scope-full-pr").click();
    await expect(page.getByTestId("change-review")).toBeVisible();

    // Client-side switch through the sidebar run list.
    await page.locator(`a.ws-row[href^="${runPaths[1]}"]`).first().click();
    await page.waitForURL((u) => u.pathname === runPaths[1]);
    const diffTab = page.getByRole("button", { name: "Diff", exact: true });
    if ((await diffTab.getAttribute("aria-pressed")) !== "true") await diffTab.click();
    await expect(page.getByTestId("diff-scope-since-last")).toHaveAttribute("aria-pressed", "true", { timeout: 30_000 });
    await expect(page.getByTestId("change-review")).toHaveCount(0);
  });
});

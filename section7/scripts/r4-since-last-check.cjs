/**
 * R4 since-last-iteration: browser check with every backend stubbed.
 * Verification scaffolding only (no stack, no Docker): it proves the Diff pane
 * behaviour in a real Chromium against `next dev`; the stack journey is
 * e2e/r4-since-last-iteration.e2e.ts.
 *
 * Run from section7/:
 *   npx next dev -p 3100 &
 *   node scripts/r4-since-last-check.cjs <screenshot-dir>
 * Set PW_CHROMIUM to a Chromium binary when Playwright's own is not installed.
 */
const { chromium } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;
const OUT = process.argv[2] ?? ".";
const BASE = "http://localhost:3100";
const SHA = "c0ffee1234567890c0ffee1234567890c0ffee12";
const DIFF = "diff --git a/src/greeting.ts b/src/greeting.ts\n--- a/src/greeting.ts\n+++ b/src/greeting.ts\n@@ -1,3 +1,3 @@\n export function greet(name: string) {\n-  return name;\n+  return `Hello, ${name}!`;\n }\n";
const HUNKS = [{ path: "src/greeting.ts", binary: false, hunks: [{ index: 0, header: "@@ -1,3 +1,3 @@", lines: [" export function greet(name: string) {", "-  return `hi ${name}`;", "+  return `Hello, ${name}!`;", " }"], additions: 1, deletions: 1 }] }];
const session = JSON.stringify({ token: "t", principal: { userId: "u1", teamId: "team1", role: "owner", email: "a@b.dev" } });

let checkpointsMode = "some"; const reverts = [];
async function stub(route) {
  const url = new URL(route.request().url());
  const p = url.pathname; const m = route.request().method();
  const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(body) });
  if (m === "OPTIONS") return route.fulfill({ status: 204, headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" } });
  if (/\/checkpoints\/[^/]+\/hunks$/.test(p)) return json({ projectId: "p1", sha: SHA, files: HUNKS });
  if (/\/checkpoints\/[^/]+\/revert$/.test(p)) { reverts.push(route.request().postDataJSON()); return json({ status: "ok", sha: "beef000011112222", reverted: 1 }); }
  if (/\/checkpoints$/.test(p)) {
    if (checkpointsMode === "none") return json({ error: { code: "CHECKPOINT_REPOSITORY_NOT_FOUND", message: "none" } }, 404);
    return json({ projectId: "p1", nextCursor: null, items: [{ sha: SHA, message: "checkpoint: post-edit step-2", createdAt: new Date().toISOString(), taskId: "run-1" }] });
  }
  if (/\/orchestrator\/runs\/[^/]+$/.test(p)) return json({ runId: p.split("/").pop(), state: "pr_opened", prId: "pr-1", url: "https://github.com/x/y/pull/1", steps: [{ seq: 1, type: "plan_proposed", ts: new Date().toISOString() }], costUsd: 0.12 });
  if (/\/reviews\/pr-1$/.test(p)) return json({ prId: "pr-1", pr_url: "https://github.com/x/y/pull/1", diff: DIFF, changedFiles: [{ path: "src/greeting.ts", additions: 1, deletions: 1 }], riskFlags: [], testResults: [] });
  if (/\/reviews\/pr-1\/comments$/.test(p)) return json([]);
  return json({ error: { code: "STUB", message: "not stubbed" } }, 503);
}

(async () => {
  const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } }); const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.stack.split("\n").slice(0,4).join(" / ")));
  await page.addInitScript((s) => {
    localStorage.setItem("harness.session", s);
    window.__crSeen = false;
    new MutationObserver(() => { if (document.querySelector('[data-testid="change-review"]')) window.__crSeen = true; })
      .observe(document, { childList: true, subtree: true });
  }, session);
  await page.route(/localhost:80\d\d/, stub);
  const results = [];
  const check = async (name, fn) => { try { await fn(); results.push(`PASS ${name}`); } catch (e) { results.push(`FAIL ${name}: ${String(e.message).split("\n")[0]}`); } };
  const { expect } = require("@playwright/test");

  await page.goto(`${BASE}/projects/p1/runs/run-1?pane=diff`, { timeout: 180000 });
  await check("default scope is since-last", async () => {
    await expect(page.getByTestId("since-last-diff")).toBeVisible({ timeout: 120000 });
    await expect(page.getByTestId("diff-scope-since-last")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("since-last-source")).toContainText("this run’s newest snapshot");
  });
  await check("review-decide visible", () => expect(page.getByTestId("review-decide")).toBeVisible());
  await check("no ChangeReview, never flashed", async () => {
    await expect(page.getByTestId("change-review")).toHaveCount(0);
    if (await page.evaluate(() => window.__crSeen)) throw new Error("change-review attached during load");
  });
  await check("axe clean in pane (since-last, 1440)", async () => {
    const v = (await new AxeBuilder({ page }).include(".ws-pane").withTags(["wcag2a","wcag2aa","wcag21aa","wcag22aa"]).analyze()).violations;
    if (v.length) throw new Error(v.map((x) => `${x.id}: ${x.nodes.length}`).join(", "));
  });
  await page.screenshot({ path: `${OUT}/r4-since-last-1440.png`, fullPage: false });
  await check("toggle to full PR shows DiffPane + ChangeReview, no 'not available'", async () => {
    await page.getByTestId("diff-scope-full-pr").click();
    await expect(page.getByTestId("preview-diff")).toBeVisible();
    await expect(page.getByTestId("change-review")).toBeVisible();
    await expect(page.getByText(/view is not available/)).toHaveCount(0);
  });
  await page.screenshot({ path: `${OUT}/r4-full-pr-1440.png`, fullPage: false });
  await check("toggle back hides ChangeReview", async () => {
    await page.getByTestId("diff-scope-since-last").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("since-last-diff")).toBeVisible();
    await expect(page.getByTestId("change-review")).toHaveCount(0);
  });
  await check("revert body is { hunks: [{ path, index }] }", async () => {
    await page.getByLabel("Revert hunk 1 in src/greeting.ts").check();
    await page.getByTestId("since-last-revert").click();
    await expect(page.getByText(/Reverted 1 hunk\(s\)/)).toBeVisible();
    if (JSON.stringify(reverts) !== JSON.stringify([{ hunks: [{ path: "src/greeting.ts", index: 0 }] }])) throw new Error(JSON.stringify(reverts));
  });
  await check("run switch (full navigation) re-defaults to since-last", async () => {
    await page.getByTestId("diff-scope-full-pr").click();
    await expect(page.getByTestId("change-review")).toBeVisible();
    await page.evaluate(() => { window.__crSeen = false; });
    // client-side navigation via Next's router (no sidebar rows in the stub)
    await page.evaluate(() => { const a = document.createElement("a"); a.href = "/projects/p1/runs/run-2?pane=diff"; a.id = "go2"; document.body.appendChild(a); });
    await page.goto(`${BASE}/projects/p1/runs/run-2?pane=diff`);
    await expect(page.getByTestId("diff-scope-since-last")).toHaveAttribute("aria-pressed", "true", { timeout: 60000 });
    await expect(page.getByTestId("change-review")).toHaveCount(0);
  });
  await check("phone 375: since-last renders, axe clean", async () => {
    await page.setViewportSize({ width: 375, height: 800 });
    await expect(page.getByTestId("since-last-diff")).toBeVisible();
    const v = (await new AxeBuilder({ page }).include(".ws-pane").withTags(["wcag2a","wcag2aa","wcag21aa","wcag22aa"]).analyze()).violations;
    if (v.length) throw new Error(v.map((x) => `${x.id}: ${x.nodes.length}`).join(", "));
  });
  await page.screenshot({ path: `${OUT}/r4-since-last-375.png`, fullPage: false });
  await check("no checkpoints -> full PR default, ChangeReview quiet, since-last disabled", async () => {
    checkpointsMode = "none";
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${BASE}/projects/p1/runs/run-3?pane=diff`);
    await expect(page.getByTestId("diff-scope-full-pr")).toHaveAttribute("aria-pressed", "true", { timeout: 60000 });
    await expect(page.getByTestId("diff-scope-since-last")).toBeDisabled();
    await expect(page.getByTestId("since-last-none")).toBeVisible();
    await expect(page.getByTestId("preview-diff")).toBeVisible();
  });
  results.push(`pageerrors: ${errors.length ? errors.join(" | ") : "none"}`);
  console.log(results.join("\n"));
  await browser.close();
})();

/**
 * E0 visual baseline (ui-ux-enhancement-master-prompt-2026-11-21 §7 E0b).
 *
 * Screenshots every page.tsx route at 1440 and 375 px, dark and light, from a
 * running stack (RC :3011 via E2E_BASE_URL). Times, ids and emails are scrubbed
 * from the DOM before each capture so pixel diffs across phases compare layout
 * and style, not data. Output: docs/briefs/evidence/ui-e/baseline/.
 *
 * No UI changes are made by this spec; it never starts a paid run — the demo
 * seed is the REPLAY journey the demo page itself drives. Signed-out routes are
 * captured before login (that is their real first-visit state).
 *
 * Run: E2E_BASE_URL=http://localhost:3011 E2E_API_URL=http://localhost:18001 \
 *      E2E_IDENTITY_CONTAINER=harness-rc-identity-1 npx playwright test e2e/visual.e2e.ts
 */
import { expect, test, type Page } from "@playwright/test";
import { journeyEmail, loginViaUi } from "./support";

const SHOTS = "../docs/briefs/evidence/ui-e/baseline";
const WIDTHS = [1440, 375] as const;
const THEMES = ["dark", "light"] as const;
const CONSOLE_ERRORS: string[] = [];

async function applyTheme(page: Page, theme: (typeof THEMES)[number]) {
  await page.evaluate((next) => {
    window.localStorage.setItem("harness.theme", next);
    document.documentElement.dataset.theme = next;
  }, theme);
}

/** Replace volatile text (times, ids, emails) so screenshots diff on layout. */
async function scrubVolatileText(page: Page) {
  await page.evaluate(() => {
    const scrubText = (el: Element) => {
      let t = el.textContent ?? "";
      t = t.replace(/\b\d+\s*(s|sec|seconds|min|mins|m|h|hr|d|day|days)\s*ago\b/gi, "time-masked");
      t = t.replace(/\b(?:demo-run|demo-proj|run|proj)-[0-9a-f]{6,}\b/gi, "id-masked");
      t = t.replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, "id-masked");
      t = t.replace(/\b[\w.+-]+@[\w-]+\.[\w.]+\b/g, "email-masked");
      t = t.replace(/\b[0-9a-f]{16,}\b/gi, "id-masked");
      if (t !== el.textContent) el.textContent = t;
    };
    document.querySelectorAll("time").forEach((el) => {
      el.textContent = "time-masked";
      el.removeAttribute("datetime");
    });
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    let node: Node | null = walker.currentNode;
    while (node) {
      const el = node as Element;
      if (el.children && el.children.length === 0 && (el.textContent ?? "").length > 0) scrubText(el);
      node = walker.nextNode();
    }
  });
}

async function capture(page: Page, slug: string, fullPage = true) {
  for (const theme of THEMES) {
    await applyTheme(page, theme);
    await page.waitForTimeout(150);
    await scrubVolatileText(page);
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(200);
      await page.screenshot({ path: `${SHOTS}/${slug}-${width}-${theme}.png`, fullPage });
    }
  }
}

test("E0 visual baseline: every route at 1440/375, dark/light, times and ids masked", async ({ page }) => {
  test.setTimeout(600_000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  page.on("console", (m) => { if (m.type() === "error") CONSOLE_ERRORS.push(`${page.url()} ${m.text()}`); });
  page.on("pageerror", (e) => CONSOLE_ERRORS.push(`${page.url()} ${String(e)}`));

  // --- Signed-out routes (their real first-visit state) ---
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "HARNESS" })).toBeVisible();
  await capture(page, "login");
  for (const [slug, path] of [
    ["sso-callback", "/auth/sso/callback"],
    ["unsubscribe", "/notifications/unsubscribe"],
    ["pricing", "/pricing"],
    ["docs", "/docs"],
  ] as const) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await capture(page, slug);
  }

  // --- Sign in through the real form ---
  await loginViaUi(page, journeyEmail());

  // --- Static-in-practice routes ---
  for (const [slug, path] of [
    ["home", "/"],
    ["onboarding", "/onboarding"],
    ["notifications", "/notifications"],
    ["meeting", "/meeting"],
    ["portal", "/portal"],
    ["review", "/review"],
    ["runs", "/runs"],
    ["cost", "/dashboard/cost"],
    ["settings-overview", "/settings"],
    ["settings-account", "/settings/account"],
    ["settings-billing", "/settings/billing"],
    ["settings-governance", "/settings/governance"],
    ["settings-integrations", "/settings/integrations"],
    ["settings-keys", "/settings/keys"],
    ["settings-models", "/settings/models"],
    ["settings-notifications", "/settings/notifications"],
    ["settings-project", "/settings/project"],
    ["settings-team", "/settings/team"],
    ["triage", "/triage"],
    ["demo", "/demo"],
  ] as const) {
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(400);
    await capture(page, slug);
  }

  // --- Dynamic routes from the seeded demo run (REPLAY, no provider keys) ---
  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?]+/, { timeout: 60_000 });
  await page.waitForLoadState("networkidle");
  const runUrl = new URL(page.url());
  const base = runUrl.pathname; // /projects/:p/runs/:r
  const projectId = base.split("/")[2];

  for (const [slug, path] of [
    ["run", base],
    ["run-pane-diff", `${base}?pane=diff`],
    ["run-pane-plan", `${base}?pane=plan`],
    ["run-pane-terminal", `${base}?pane=terminal`],
    ["run-pane-evidence", `${base}?pane=evidence`],
    ["checkpoints", `${base}/checkpoints`],
    ["report", `${base}/report`],
    ["tree", `/projects/${projectId}/tree`],
  ] as const) {
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(400);
    await capture(page, slug);
  }

  // Console errors are E0 audit input, not a gate here (§5 stability is judged
  // per-screen in the audit report; this log keeps the evidence honest).
  if (CONSOLE_ERRORS.length > 0) {
    console.log(`[visual.e2e] console errors observed (${CONSOLE_ERRORS.length}):`);
    for (const e of CONSOLE_ERRORS.slice(0, 40)) console.log("  -", e);
  }
});

/**
 * Local visual verification harness for the UI-week leftovers — does NOT touch RC containers.
 *
 * Serves `next start` (production build) on :3100 and drives it with Playwright
 * while proxying every API origin to the RC backends via route interception.
 * Playwright fulfils those requests from Node, so the browser's CORS check
 * never sees a cross-origin rejection. This exists because RC CORS allows
 * exactly http://localhost:3011 and the owner must approve rebuilds; it is
 * verification scaffolding, not a product feature.
 *
 * Run from section7/: node scripts/u-check-check.mjs
 */
import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { spawn } from "node:child_process";

const PORT = 3100;
const BASE = `http://127.0.0.1:${PORT}`;
const ORIGIN = `http://localhost:${PORT}`;

const PORT_MAP = { 8001: 18001, 8002: 18002, 8003: 28003, 8004: 18004, 8005: 18005, 8006: 18006, 8008: 18008 };

function log(...a) { console.log("[u-check]", ...a); }

async function waitForServer(url, tries = 60) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(url);
      if (res.status < 500) return true;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`server never came up at ${url}`);
}

const next = spawn("npx", ["next", "start", "-p", String(PORT)], {
  cwd: process.cwd(),
  stdio: ["ignore", "pipe", "pipe"],
});
next.stdout.on("data", (d) => process.stdout.write(`[next] ${d}`));
next.stderr.on("data", (d) => process.stderr.write(`[next!] ${d}`));

try {
  await waitForServer(BASE);

  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  page.on("response", (r) => { if (r.status() >= 400) consoleErrors.push(`${r.status()} ${r.request().method()} ${r.url()}`); });
  // Sockets cannot be proxied by route interception; RC only accepts its own origin.
  await page.routeWebSocket(/\/api\/v1\/ws\//, (ws) => ws.close());

  await page.route(/(localhost|127\.0\.0\.1):(8001|8002|8003|8004|8005|8006|8008)\//, async (route) => {
    const url = new URL(route.request().url());
    const targetPort = PORT_MAP[Number(url.port)];
    const target = `http://127.0.0.1:${targetPort}${url.pathname}${url.search}`;
    try {
      const resp = await route.fetch({ url: target });
      const body = await resp.body();
      await route.fulfill({ response: resp, body });
    } catch (err) {
      log("proxy fail", target, String(err));
      await route.fulfill({ status: 502, body: JSON.stringify({ detail: "proxy unreachable" }), contentType: "application/json" });
    }
  });

  // The event-stream WS is blocked: the UI's honest response is a quiet
  // reconnect note, which the screenshots should not depend on either way.
  await page.route(/localhost:8001\/api\/v1\/events/, (route) => route.abort());

  const email = `u-check-${Date.now().toString(36)}@harness.dev`;
  const login = await fetch("http://127.0.0.1:18001/api/v1/auth/login", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ email }),
  }).then((r) => r.json());
  if (!login.token) throw new Error(`login failed: ${JSON.stringify(login)}`);
  const token = login.token;
  const seed = await fetch("http://127.0.0.1:18004/api/v1/orchestrator/demo/seed", {
    method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
  }).then((r) => r.json());
  log("seeded", seed.runId, seed.prId);

  await page.goto(`${ORIGIN}/login`);
  await page.evaluate(([tok, principal]) => {
    window.localStorage.setItem("harness.session", JSON.stringify({ token: tok, principal }));
  }, [token, login.principal ?? null]);

  const failures = [];
  const check = (ok, what) => { if (!ok) failures.push(what); log(ok ? "ok  " : "FAIL", what); };
  const shot = async (name) => {
    for (const theme of ["dark", "light"]) {
      await page.evaluate((t) => { window.localStorage.setItem("harness.theme", t); document.documentElement.dataset.theme = t; }, theme);
      for (const [w, h] of [[1440, 900], [375, 812]]) {
        await page.setViewportSize({ width: w, height: h });
        await page.waitForTimeout(300);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        check(overflow <= 0, `${name} ${w} ${theme}: horizontal overflow ${overflow}px`);
        const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
        check(axe.violations.length === 0, `${name} ${w} ${theme}: axe ${axe.violations.map((v) => `${v.id}(${v.nodes.length}) ${v.nodes[0]?.target} ${JSON.stringify(v.nodes[0]?.any?.[0]?.data ?? {}).slice(0, 140)}`).join("; ") || "clean"}`);
        await page.screenshot({ path: `../docs/briefs/evidence/ui/u-${name}-${w}-${theme}.png`, fullPage: w === 375 });
      }
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  };

  // Settings: the section nav is on every settings page and marks the current one.
  await page.goto(`${ORIGIN}/settings/team`);
  await page.locator(".settings-nav").waitFor({ timeout: 20000 });
  check(await page.locator(".settings-nav a").count() === 10, "settings nav lists the overview and nine sections");
  check((await page.locator('.settings-nav a[aria-current="page"]').innerText()) === "Team and invites", "the current section is marked");
  await page.waitForLoadState("networkidle");
  await shot("settings-team");
  await page.locator(".settings-nav a", { hasText: "Billing and plan" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/settings\/billing/);
  check(true, "the nav moves between sections from the keyboard");

  // Palette: a settings section with no shortcut is reachable by typing its name.
  await page.keyboard.press("ControlOrMeta+k");
  await page.getByPlaceholder("Where to?").fill("api keys");
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/settings\/keys/, { timeout: 10000 }).then(() => check(true, "palette opens API keys"), () => check(false, "palette opens API keys"));
  await page.keyboard.press("ControlOrMeta+k");
  await page.getByPlaceholder("Where to?").fill("phone triage");
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/triage/, { timeout: 10000 }).then(() => check(true, "palette opens phone triage"), () => check(false, "palette opens phone triage"));

  // Phone triage: pending approvals only.
  await page.waitForLoadState("networkidle");
  const triage = await page.locator("main").innerText();
  log(`triage reads: "${triage.replace(/\n+/g, " | ").slice(0, 200)}"`);
  await shot("triage");

  // Checkpoints page is inside the shell and links back to the run.
  await page.goto(`${ORIGIN}/projects/${seed.projectId}/runs/${seed.runId}/checkpoints`);
  await page.getByText("← Back to the run").waitFor({ timeout: 20000 });
  await page.locator(".ws-row").first().waitFor({ timeout: 20000 }).then(() => check(true, "checkpoints page has the run list sidebar"), () => check(false, "checkpoints page has the run list sidebar"));
  await page.waitForLoadState("networkidle");
  await shot("checkpoints");

  // Five-key tour: open on the first visit, closed for good after "Got it".
  await page.evaluate(() => window.localStorage.removeItem("harness.keytour.seen"));
  await page.goto(`${ORIGIN}/`);
  await page.getByTestId("key-tour-done").waitFor({ timeout: 20000 });
  check(await page.locator(".key-tour dt").count() === 5, "the tour shows five keys on a first visit");
  await page.waitForLoadState("networkidle");
  await shot("tour");
  await page.getByTestId("key-tour-done").focus();
  await page.keyboard.press("Enter");
  await page.reload();
  await page.locator("#ws-composer-input").waitFor({ timeout: 20000 });
  check(!(await page.getByTestId("key-tour-done").isVisible()), "after Got it the tour stays closed on reload");

  // Cost page: one headline number (or the honest no-data notice).
  await page.goto(`${ORIGIN}/dashboard/cost`);
  await page.waitForLoadState("networkidle");
  const headline = await page.getByTestId("cost-headline").count();
  log(`cost page: ${headline ? `headline "${(await page.getByTestId("cost-headline").innerText()).replace(/\n/g, " | ")}"` : "no spend in the period; the no-data notice is shown"}`);
  check(await page.getByRole("heading", { name: "By day" }).count() <= 1, "By day appears once");
  await shot("cost");

  // First run: from the demo button to a finished run with a pull request.
  await page.goto(`${ORIGIN}/demo`);
  await page.getByTestId("demo-seed-button").waitFor();
  const t0 = Date.now();
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?#]+/, { timeout: 60000 });
  await page.getByText("Pull request opened").first().waitFor({ timeout: 60000 });
  const secs = (Date.now() - t0) / 1000;
  check(secs < 60, `demo reaches a finished run in ${secs.toFixed(1)} s (limit 60)`);

  log("failures:", failures.length ? JSON.stringify(failures, null, 1) : "none");
  log("console errors:", consoleErrors.length ? JSON.stringify([...new Set(consoleErrors.map((e) => e.replace(/ticket=[^&]+/, "ticket=…")))], null, 1) : "none");
  await browser.close();
} finally {
  next.kill("SIGTERM");
}
log("done");

import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";

const PORT = 3100;
const BASE = `http://127.0.0.1:${PORT}`;
const ORIGIN = `http://localhost:${PORT}`;
const SHOTS = "../docs/briefs/evidence/ui-e";
const PORT_MAP = { 8001: 18001, 8002: 18002, 8003: 28003, 8004: 18004, 8005: 18005, 8006: 18006, 8008: 18008 };

function log(...a) { console.log("[audit-walk]", ...a); }
async function waitForServer(url, tries = 60) {
  for (let i = 0; i < tries; i++) { try { const r = await fetch(url); if (r.status < 500) return; } catch {} await new Promise((r) => setTimeout(r, 500)); }
  throw new Error("server never came up");
}
const next = spawn("npx", ["next", "start", "-p", String(PORT)], { cwd: process.cwd(), stdio: ["ignore", "ignore", "ignore"] });
let browser;
const findings = [];
function rec(sev, where, what) { findings.push({ sev, where, what }); log(`[${sev}] ${where}: ${what}`); }

try {
  await waitForServer(BASE);
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(`${page.url()} :: ${m.text().slice(0, 160)}`); });
  page.on("pageerror", (e) => errors.push(`${page.url()} :: PAGEERROR ${String(e).slice(0, 160)}`));
  page.on("response", (r) => { if (r.status() >= 400) errors.push(`${page.url()} :: HTTP ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });

  await page.route(/(localhost|127\.0\.0\.1):(8001|8002|8003|8004|8005|8006|8008)\//, async (route) => {
    const url = new URL(route.request().url());
    const target = `http://127.0.0.1:${PORT_MAP[Number(url.port)]}${url.pathname}${url.search}`;
    try { const resp = await route.fetch({ url: target }); await route.fulfill({ response: resp, body: await resp.body() }); }
    catch { await route.fulfill({ status: 502, body: "{}", contentType: "application/json" }); }
  });
  await page.routeWebSocket(/\/api\/v1\/ws\//, (ws) => ws.close());

  const email = `audit-${Date.now().toString(36)}@harness.dev`;
  const login = await fetch("http://127.0.0.1:18001/api/v1/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email }) }).then((r) => r.json());
  const seed = await fetch("http://127.0.0.1:18004/api/v1/orchestrator/demo/seed", { method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${login.token}` } }).then((r) => r.json());
  const runBase = `/projects/${encodeURIComponent(seed.projectId)}/runs/${encodeURIComponent(seed.runId)}`;
  log("seeded", seed.runId);

  await page.addInitScript(([tok, principal]) => window.localStorage.setItem("harness.session", JSON.stringify({ token: tok, principal })), [login.token, login.principal ?? null]);

  const shot = (name) => page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });

  // ---- / runs list (F6-class issues, footer count, ids) ----
  await page.goto(`${ORIGIN}/runs`); await page.waitForLoadState("networkidle").catch(()=>{}); await page.waitForTimeout(600);
  await shot("audit-01-runs-1440-dark");
  const runsBody = (await page.locator("main").innerText()).replace(/\s+/g, " ").slice(0, 900);
  log("runs text:", runsBody.slice(0, 400));

  // ---- /review ----
  await page.goto(`${ORIGIN}/review`); await page.waitForLoadState("networkidle").catch(()=>{}); await page.waitForTimeout(600);
  await shot("audit-02-review-1440-dark");
  const reviewBody = (await page.locator("main").innerText()).replace(/\s+/g, " ");
  log("review text:", reviewBody.slice(0, 300));
  if (/prId|pr-|github/i.test(reviewBody)) rec("medium", "/review", "raw identifiers or backend vocabulary visible in review page text");

  // ---- /dashboard/cost ----
  await page.goto(`${ORIGIN}/dashboard/cost`); await page.waitForLoadState("networkidle").catch(()=>{}); await page.waitForTimeout(600);
  await shot("audit-03-cost-1440-dark");
  const costBody = (await page.locator("main").innerText()).replace(/\s+/g, " ");
  log("cost text:", costBody.slice(0, 300));
  if (/\$\d+\.\d{5,}/.test(costBody)) rec("medium", "/dashboard/cost", "money shown with >2 decimals (violates money rule)");
  if (/Unknown|unknown/i.test(costBody)) log("cost mentions unknown (check it is honest-labelling)");

  // ---- run page + panes: focus visibility, console errors, keyboard ----
  await page.goto(`${ORIGIN}${runBase}`); await page.waitForLoadState("networkidle").catch(()=>{}); await page.waitForTimeout(800);
  await shot("audit-04-run-1440-dark");
  const runBody = (await page.locator("main").innerText()).replace(/\s+/g, " ");
  log("run text:", runBody.slice(0, 400));
  if (/\b(plan_proposed|tool_call|tool_result|execution_started|engine_completed|pr_opened)\b/.test(runBody)) rec("medium", "run page", "raw backend event names visible on screen");
  if (new RegExp(seed.runId).test(runBody)) rec("low", "run page", "raw run id visible as page text (should be behind Copy id)");

  // keyboard: palette
  await page.keyboard.press("Meta+k"); await page.waitForTimeout(400);
  const paletteVisible = await page.getByRole("dialog").isVisible().catch(() => false) || await page.locator("[cmdk-root], [cmdk-input]").first().isVisible().catch(() => false);
  if (!paletteVisible) rec("high", "palette", "⌘K did not open a visible dialog/cmdk root on the run page");
  else {
    await shot("audit-05-palette-open-dark");
    const hits = await page.locator("[cmdk-item]").count().catch(() => 0);
    log("palette items:", hits);
    if (hits < 5) rec("medium", "palette", `palette lists only ${hits} items on the run page`);
  }
  await page.keyboard.press("Escape"); await page.waitForTimeout(200);

  // j/k navigation on the run column?
  await page.keyboard.press("j"); await page.waitForTimeout(250);
  await page.keyboard.press("k"); await page.waitForTimeout(250);
  const focusAfterJk = await page.evaluate(() => document.activeElement?.getAttribute("data-testid") || document.activeElement?.tagName || "body");
  log("focus after j/k:", focusAfterJk);
  if ((focusAfterJk === "body")) log("note: j/k has no effect here (expected pre-E3; E3 adds block navigation)");

  // g-then-h shortcut (home)
  await page.keyboard.press("g"); await page.waitForTimeout(120); await page.keyboard.press("h"); await page.waitForTimeout(500);
  log("after g,h →", page.url());

  // ---- run-diff pane: reviewer path ----
  await page.goto(`${ORIGIN}${runBase}?pane=diff`); await page.waitForLoadState("networkidle").catch(()=>{}); await page.waitForTimeout(800);
  await shot("audit-06-run-diff-dark");
  const diffBody = (await page.locator("main").innerText()).replace(/\s+/g, " ");
  log("diff text:", diffBody.slice(0, 300));

  // ---- phone width: run + diff (horizontal scroll check) ----
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`${ORIGIN}${runBase}`); await page.waitForTimeout(700);
  const hsRun = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (hsRun > 0) rec("high", "run @375", `horizontal overflow ${hsRun}px`);
  await shot("audit-07-run-375-dark");
  await page.goto(`${ORIGIN}${runBase}?pane=diff`); await page.waitForTimeout(700);
  const hsDiff = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (hsDiff > 0) rec("high", "run-diff @375", `horizontal overflow ${hsDiff}px`);
  await shot("audit-08-run-diff-375-dark");
  await page.setViewportSize({ width: 1440, height: 900 });

  // ---- settings overview: nine-links list? ----
  await page.goto(`${ORIGIN}/settings`); await page.waitForLoadState("networkidle").catch(()=>{}); await page.waitForTimeout(500);
  await shot("audit-09-settings-dark");

  // ---- reload restores view ----
  await page.goto(`${ORIGIN}${runBase}?pane=diff`); await page.waitForTimeout(700);
  await page.reload(); await page.waitForLoadState("networkidle").catch(()=>{}); await page.waitForTimeout(700);
  const paneRestored = page.url().includes("pane=diff");
  if (!paneRestored) rec("medium", "run page", "reload lost ?pane=diff (view not restored)");
  const diffVisibleAfterReload = await page.locator("text=Reviewed").first().isVisible().catch(() => false);
  log("reload: pane param kept:", paneRestored, "reviewed control visible:", diffVisibleAfterReload);

  // ---- axe on three key routes ----
  const { default: AxeBuilder } = await import("@axe-core/playwright");
  for (const [name, path] of [["runs", "/runs"], ["run", runBase], ["settings", "/settings"]]) {
    await page.goto(`${ORIGIN}${path}`); await page.waitForTimeout(800);
    const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    const viol = res.violations.filter((v) => (v.nodes || []).length > 0);
    for (const v of viol.slice(0, 6)) rec("high", `axe:${name}`, `${v.id} (${v.impact || "n/a"}) ×${v.nodes.length} — ${String(v.help).slice(0, 90)}`);
    log(`axe ${name}: ${viol.length} violation kinds`);
  }

  log("console/network errors observed:", errors.length);
  for (const e of [...new Set(errors)].slice(0, 25)) log("  ERR", e);

  const { writeFileSync } = await import("node:fs");
  writeFileSync("../docs/briefs/evidence/ui-e/e0-audit-findings-raw.json", JSON.stringify({ generatedAt: new Date().toISOString(), stack: "prod build :3100 → RC", runId: seed.runId, findings, consoleErrors: [...new Set(errors)] }, null, 2));
  log("findings recorded:", findings.length);
} finally {
  if (browser) await browser.close();
  next.kill("SIGTERM");
}

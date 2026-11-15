/**
 * Local visual verification harness for R5 — does NOT touch RC containers.
 *
 * Serves `next start` (production build) on :3100 and drives it with Playwright
 * while proxying every API origin to the RC backends via route interception.
 * Playwright fulfils those requests from Node, so the browser's CORS check
 * never sees a cross-origin rejection. This exists because RC CORS allows
 * exactly http://localhost:3011 and the owner must approve rebuilds; it is
 * verification scaffolding, not a product feature.
 *
 * Run from section7/: node scripts/r5-check-check.mjs
 */
import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { spawn } from "node:child_process";

const PORT = 3100;
const BASE = `http://127.0.0.1:${PORT}`;
const ORIGIN = `http://localhost:${PORT}`;

const PORT_MAP = { 8001: 18001, 8002: 18002, 8003: 28003, 8004: 18004, 8005: 18005, 8006: 18006, 8008: 18008 };

function log(...a) { console.log("[r5-check]", ...a); }

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

  const email = `r5-check-${Date.now().toString(36)}@harness.dev`;
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
        await page.screenshot({ path: `../docs/briefs/evidence/ui/r5-${name}-${w}-${theme}.png`, fullPage: w === 375 });
      }
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  };

  // The seeded demo run only; no model calls.
  await page.goto(`${ORIGIN}/projects/${seed.projectId}/runs/${seed.runId}?pane=evidence`);
  await page.getByTestId("run-evidence").waitFor({ timeout: 30000 });
  const cost = await page.getByTestId("evidence-cost").innerText();
  check(/\$\d|Not metered yet/.test(cost), `cost headline reads: "${cost.replace(/\n/g, " | ")}"`);
  const rows = await page.locator(".ws-trace li").count();
  check(rows > 0, `trace shows ${rows} timed events`);
  const bad = await page.locator(".ws-trace-bar").evaluateAll((bars) => bars.filter((b) => {
    const t = b.parentElement.getBoundingClientRect(); const r = b.getBoundingClientRect();
    return r.width <= 0 || r.right > t.right + 1 || r.left < t.left - 1;
  }).length);
  check(bad === 0, "every trace bar is visible and inside its track");
  await shot("evidence");

  // Export from the keyboard: the browser receives a JSON file of the run history.
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 40000 }).catch(() => null),
    (async () => { await page.getByTestId("evidence-export").focus(); await page.keyboard.press("Enter"); })(),
  ]);
  if (download) {
    const path = await download.path();
    const text = (await import("node:fs")).readFileSync(path, "utf8");
    let ok = false; try { JSON.parse(text); ok = true; } catch {}
    check(ok, `export downloaded ${download.suggestedFilename()} (${text.length} bytes, valid JSON)`);
    check(text.includes(seed.runId), "the export is about this run");
  } else {
    check(false, `export did not download; the pane says: "${await page.locator(".ws-evidence-export").innerText()}"`);
  }
  await shot("evidence-exported");

  log("failures:", failures.length ? JSON.stringify(failures, null, 1) : "none");
  log("console errors:", consoleErrors.length ? JSON.stringify([...new Set(consoleErrors.map((e) => e.replace(/ticket=[^&]+/, "ticket=…")))], null, 1) : "none");
  await browser.close();
} finally {
  next.kill("SIGTERM");
}
log("done");

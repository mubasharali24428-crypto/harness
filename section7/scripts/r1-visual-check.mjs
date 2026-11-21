/**
 * Local visual verification harness for R1 — does NOT touch RC containers.
 *
 * Serves `next start` (production build) on :3100 and drives it with Playwright
 * while proxying every API origin to the RC backends via route interception.
 * Playwright fulfils those requests from Node, so the browser's CORS check
 * never sees a cross-origin rejection. This exists because RC CORS allows
 * exactly http://localhost:3011 and the owner must approve rebuilds; it is
 * verification scaffolding, not a product feature.
 *
 * Run from section7/: node scripts/r1-visual-check.mjs
 */
import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";

const PORT = 3100;
const BASE = `http://127.0.0.1:${PORT}`;
const ORIGIN = `http://localhost:${PORT}`;

const PORT_MAP = { 8001: 18001, 8004: 18004, 8005: 18005, 8008: 18008 };

function log(...a) { console.log("[r1-visual]", ...a); }

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

  await page.route(/(localhost|127\.0\.0\.1):(8001|8004|8005|8008)\//, async (route) => {
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

  const email = `r1-visual-${Date.now().toString(36)}@harness.dev`;
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

  const routes = [
    ["run", `/preview/run?run=${encodeURIComponent(seed.runId)}`],
    ["diff", `/preview/diff?run=${encodeURIComponent(seed.runId)}`],
    ["review", `/preview/review?run=${encodeURIComponent(seed.runId)}`],
    ["run-missing", "/preview/run?run=does-not-exist-xyz"],
    ["signed-out", null],
  ];
  const themes = ["dark", "light"];

  for (const theme of themes) {
    await page.evaluate((t) => window.localStorage.setItem("harness.theme", t), theme);
    for (const size of [[1440, 900], [375, 812]]) {
      await page.setViewportSize({ width: size[0], height: size[1] });
      for (const [name, url] of routes) {
        if (name === "signed-out") {
          if (size[0] !== 1440 || theme !== "dark") continue;
          await page.evaluate(() => window.localStorage.removeItem("harness.session"));
          await page.goto(`${ORIGIN}/preview/run`);
        } else {
          await page.evaluate(([tok, principal]) => {
            window.localStorage.setItem("harness.session", JSON.stringify({ token: tok, principal }));
          }, [token, login.principal ?? null]);
          await page.goto(`${ORIGIN}${url}`);
        }
        await page.waitForSelector('[data-testid="preview-workspace"]', { timeout: 20000 });
        await page.waitForTimeout(1200);
        const file = `../docs/briefs/evidence/ui/r1-${name}-${size[0]}-${theme}.png`;
        await page.screenshot({ path: file, fullPage: size[0] === 375 });
        log("shot", file);
      }
    }
  }

  log("console errors:", consoleErrors.length ? JSON.stringify(consoleErrors) : "none");
  await browser.close();
} finally {
  next.kill("SIGTERM");
}
log("done");

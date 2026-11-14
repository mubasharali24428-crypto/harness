import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { spawn } from "node:child_process";

const PORT = 3101;
const BASE = `http://127.0.0.1:${PORT}`;
const ORIGIN = `http://localhost:${PORT}`;
const PORT_MAP = { 8001: 18001, 8004: 18004, 8005: 18005, 8008: 18008 };
const next = spawn("npx", ["next", "start", "-p", String(PORT)], { cwd: process.cwd(), stdio: "ignore" });

async function wait(url) {
  for (let i = 0; i < 60; i++) { try { const r = await fetch(url); if (r.status < 500) return; } catch {} await new Promise(r=>setTimeout(r,500)); }
  throw new Error("no server");
}
try {
  await wait(BASE);
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.route(/(localhost|127\.0\.0\.1):(8001|8004|8005|8008)\//, async (route) => {
    const url = new URL(route.request().url());
    const target = `http://127.0.0.1:${PORT_MAP[Number(url.port)]}${url.pathname}${url.search}`;
    const resp = await route.fetch({ url: target });
    await route.fulfill({ response: resp, body: await resp.body() });
  });
  await page.route(/localhost:8001\/api\/v1\/ws/, (route) => route.abort());
  const email = `r1-axe-${Date.now().toString(36)}@harness.dev`;
  const login = await fetch("http://127.0.0.1:18001/api/v1/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email }) }).then(r=>r.json());
  const seed = await fetch("http://127.0.0.1:18004/api/v1/orchestrator/demo/seed", { method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${login.token}` } }).then(r=>r.json());
  await page.goto(`${ORIGIN}/login`);
  await page.evaluate(([t,p]) => window.localStorage.setItem("harness.session", JSON.stringify({ token: t, principal: p })), [login.token, login.principal]);

  const failures = {};
  for (const theme of ["dark", "light"]) {
    await page.evaluate((t) => window.localStorage.setItem("harness.theme", t), theme);
    for (const [w,h] of [[1440,900],[375,812]]) {
      await page.setViewportSize({ width: w, height: h });
      for (const route of ["run","diff","review"]) {
        await page.goto(`${ORIGIN}/preview/${route}?run=${encodeURIComponent(seed.runId)}`);
        await page.waitForSelector('[data-testid="preview-workspace"]');
        await page.waitForTimeout(800);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        const res = await new AxeBuilder({ page }).withTags(["wcag2a","wcag2aa","wcag21aa","wcag22aa"]).analyze();
        const viols = res.violations.map(v => `${v.id}(${v.impact})x${v.nodes.length}`);
        const key = `${route}-${theme}-${w}`;
        failures[key] = { overflow, viols };
      }
    }
  }
  for (const [k,v] of Object.entries(failures)) console.log(k, JSON.stringify(v));
  await browser.close();
} finally { next.kill("SIGTERM"); }

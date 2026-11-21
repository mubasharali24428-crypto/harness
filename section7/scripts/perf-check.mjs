/**
 * E0 performance baseline (ui-ux-enhancement-master-prompt-2026-11-21 §7 E0c).
 *
 * Serves the production build (`next start`, :3100) and drives it with
 * Playwright while proxying every API origin to the RC backends via route
 * interception (the r2-check.mjs pattern — RC CORS allows only :3011, and the
 * WebSocket event stream cannot be proxied, so it is closed and the UI shows
 * its honest reconnect state).
 *
 * Per route it records, with caches disabled for navigation:
 *   - LCP   (largest-contentful-paint, PerformanceObserver buffered)
 *   - CLS   (layout-shift entries, no recent-input shifts counted)
 *   - JS    (transferSize of script resources on first load)
 *   - pINP  (approximate INP from a scripted, non-mutating interaction:
 *            ⌘K opens the command palette, Escape closes it — event-timing
 *            entries with an interactionId; labelled approximate because a
 *            script cannot reproduce real user interaction variety)
 *
 * Profiles: throttle 1 (mid-range laptop proxy) for every route; throttle 4
 * (low-end phone proxy) for the budget routes §5 names: /, run, diff pane.
 *
 * Output: docs/briefs/evidence/ui-e/e0-perf-2026-11-21.{json,md}
 *
 * Run from section7/: node scripts/perf-check.mjs [--throttle4-only-budget]
 * Nothing starts a paid run; the demo seed is the REPLAY journey.
 */
import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const PORT = 3100;
const BASE = `http://127.0.0.1:${PORT}`;
const ORIGIN = `http://localhost:${PORT}`;
const SHOTS_DIR = "../docs/briefs/evidence/ui-e";
const OUT_JSON = `${SHOTS_DIR}/e0-perf-2026-11-21.json`;
const OUT_MD = `${SHOTS_DIR}/e0-perf-2026-11-21.md`;
const PORT_MAP = { 8001: 18001, 8002: 18002, 8003: 28003, 8004: 18004, 8005: 18005, 8006: 18006, 8008: 18008 };
const BUDGET_ROUTES = new Set(["landing", "run", "run-diff"]);

function log(...a) { console.log("[perf-check]", ...a); }

async function waitForServer(url, tries = 60) {
  for (let i = 0; i < tries; i++) {
    try { const res = await fetch(url); if (res.status < 500) return; } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`server never came up at ${url}`);
}

const next = spawn("npx", ["next", "start", "-p", String(PORT)], { cwd: process.cwd(), stdio: ["ignore", "pipe", "pipe"] });
next.stdout.on("data", (d) => process.stdout.write(`[next] ${d}`));
next.stderr.on("data", (d) => process.stderr.write(`[next!] ${d}`));

const results = [];
let browser;

try {
  await waitForServer(BASE);
  browser = await chromium.launch();

  const identity = "http://127.0.0.1:18001";
  const email = `perf-check-${Date.now().toString(36)}@harness.dev`;
  const login = await fetch(`${identity}/api/v1/auth/login`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ email }),
  }).then((r) => r.json());
  if (!login.token) throw new Error(`login failed: ${JSON.stringify(login)}`);
  const seed = await fetch("http://127.0.0.1:18004/api/v1/orchestrator/demo/seed", {
    method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${login.token}` },
  }).then((r) => r.json());
  log("seeded", seed.runId, seed.prId);
  const runBase = `/projects/${encodeURIComponent(seed.projectId)}/runs/${encodeURIComponent(seed.runId)}`;

  const ROUTES = [
    ["landing", "/"],
    ["login", "/login"],
    ["pricing", "/pricing"],
    ["docs", "/docs"],
    ["demo", "/demo"],
    ["onboarding", "/onboarding"],
    ["notifications", "/notifications"],
    ["meeting", "/meeting"],
    ["portal", "/portal"],
    ["review", "/review"],
    ["runs", "/runs"],
    ["cost", "/dashboard/cost"],
    ["run", runBase],
    ["run-diff", `${runBase}?pane=diff`],
    ["run-plan", `${runBase}?pane=plan`],
    ["run-terminal", `${runBase}?pane=terminal`],
    ["run-evidence", `${runBase}?pane=evidence`],
    ["checkpoints", `${runBase}/checkpoints`],
    ["report", `${runBase}/report`],
    ["tree", `/projects/${encodeURIComponent(seed.projectId)}/tree`],
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
  ];

  async function measureRoute(slug, path, throttle, viewport) {
    const ctx = await browser.newContext({ viewport });
    const cdp = await ctx.newCDPSession(ctx.pages()[0] ?? (await ctx.newPage()));
    await cdp.send("Network.enable");
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: throttle });
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
    const page = await ctx.newPage();

    await page.route(/(localhost|127\.0\.0\.1):(8001|8002|8003|8004|8005|8006|8008)\//, async (route) => {
      const url = new URL(route.request().url());
      const target = `http://127.0.0.1:${PORT_MAP[Number(url.port)]}${url.pathname}${url.search}`;
      try {
        const resp = await route.fetch({ url: target });
        await route.fulfill({ response: resp, body: await resp.body() });
      } catch {
        await route.fulfill({ status: 502, body: JSON.stringify({ detail: "proxy unreachable" }), contentType: "application/json" });
      }
    });
    await page.routeWebSocket(/\/api\/v1\/ws\//, (ws) => ws.close());
    await page.addInitScript(([tok, principal]) => {
      window.localStorage.setItem("harness.session", JSON.stringify({ token: tok, principal }));
      // Buffered metric observers must be registered before page scripts run:
      // LCP and event-timing entries are only exposed through PerformanceObserver.
      const store = { lcp: null, shifts: [], events: [] };
      Object.defineProperty(window, "__perf", { value: store, configurable: false });
      try {
        new PerformanceObserver((l) => {
          for (const e of l.getEntries()) store.lcp = Math.round(e.startTime);
        }).observe({ type: "largest-contentful-paint", buffered: true });
        new PerformanceObserver((l) => {
          store.shifts.push(...l.getEntries().filter((e) => !e.hadRecentInput));
        }).observe({ type: "layout-shift", buffered: true });
        new PerformanceObserver((l) => {
          store.events.push(...l.getEntries().filter((e) => e.interactionId));
        }).observe({ type: "event", buffered: true, durationThreshold: 16 });
      } catch {}
    }, [login.token, login.principal ?? null]);

    const t0 = Date.now();
    await page.goto(`${ORIGIN}${path}`, { waitUntil: "load", timeout: 45_000 });
    // Give the route's async chunks a beat, then read the buffered metrics.
    await page.waitForTimeout(2500);

    const metrics = await page.evaluate(() => {
      const navEntries = performance.getEntriesByType("navigation");
      const resources = performance.getEntriesByType("resource");
      const jsBytes = resources
        .filter((r) => r.initiatorType === "script" || r.initiatorType === "other" || /\.js(\?|$)/.test(r.name))
        .reduce((sum, r) => sum + (r.transferSize || 0), 0);
      const store = window.__perf ?? { lcp: null, shifts: [], events: [] };
      const pINP = store.events.length ? Math.max(...store.events.map((e) => e.duration)) : null;
      return {
        lcpMs: store.lcp,
        cls: Number(store.shifts.reduce((s, e) => s + e.value, 0).toFixed(4)),
        jsKB: Math.round(jsBytes / 1024),
        pINPms: pINP === null ? null : Math.round(pINP),
        ttfbMs: navEntries.length ? Math.round(navEntries[0].responseStart) : null,
        domContentLoadedMs: navEntries.length ? Math.round(navEntries[0].domContentLoadedEventEnd) : null,
      };
    });

    // Scripted, non-mutating interaction for pINP: open the palette (⌘K), close it (Escape).
    await page.keyboard.press("Meta+k");
    await page.waitForTimeout(400);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    const inp = await page.evaluate(() => {
      const store = window.__perf ?? { events: [] };
      return store.events.length ? Math.round(Math.max(...store.events.map((e) => e.duration))) : null;
    });

    await ctx.close();
    const row = { slug, path, throttle, viewport: `${viewport.width}x${viewport.height}`, ...metrics, pINPafterInteractMs: inp, loadMs: Date.now() - t0 };
    results.push(row);
    log(`${row.slug} t${throttle} → LCP ${row.lcpMs}ms CLS ${row.cls} JS ${row.jsKB}kB pINP ${row.pINPms ?? "-"}→${inp ?? "-"}ms`);
    return row;
  }

  const laptop = { width: 1440, height: 900 };
  const phone = { width: 375, height: 812 };

  for (const [slug, path] of ROUTES) await measureRoute(slug, path, 1, laptop);
  for (const [slug, path] of ROUTES) {
    if (BUDGET_ROUTES.has(slug)) await measureRoute(`${slug}-phone-t4`, path, 4, phone);
  }

  mkdirSync(SHOTS_DIR, { recursive: true });
  writeFileSync(OUT_JSON, JSON.stringify({ generatedAt: new Date().toISOString(), note: "pINP is approximate (scripted ⌘K/Escape interaction). Caches disabled per navigation; CPU throttle via CDP emulation.", results }, null, 2));

  const fmt = (v, unit) => (v === null || v === undefined ? "—" : `${v}${unit}`);
  const lines = [
    "# E0 performance baseline — 2026-11-21",
    "",
    "Prod build (`next start` :3100) against RC backends via request interception; caches disabled per navigation; CPU throttled via CDP. `pINP` is an **approximate INP** from a scripted, non-mutating ⌘K → Escape interaction (real INP needs human input variety). The WebSocket event stream is closed by interception, so run pages show their honest reconnect note.",
    "",
    "| Route | Profile | LCP | CLS | first-load JS | pINP | TTFB |",
    "|---|---|---|---|---|---|---|",
    ...results.map((r) => `| ${r.slug} | ${r.throttle === 1 ? "laptop t1" : "phone t4 375px"} | ${fmt(r.lcpMs, "ms")} | ${r.cls} | ${fmt(r.jsKB, "kB")} | ${fmt(r.pINPafterInteractMs ?? r.pINPms, "ms")} | ${fmt(r.ttfbMs, "ms")} |`),
    "",
    "Budget check (§5): LCP ≤ 2.0 s on / and a run page; INP ≤ 200 ms on composer/run column/diff pane; CLS ≤ 0.05.",
  ];
  const budgets = results.filter((r) => BUDGET_ROUTES.has(r.slug) || r.slug.endsWith("-phone-t4"));
  const over = [];
  for (const r of budgets) {
    if (r.lcpMs !== null && r.lcpMs > 2000) over.push(`${r.slug}: LCP ${r.lcpMs}ms > 2000ms`);
    if (r.cls > 0.05) over.push(`${r.slug}: CLS ${r.cls} > 0.05`);
    if ((r.pINPafterInteractMs ?? r.pINPms ?? 0) > 200) over.push(`${r.slug}: pINP ${r.pINPafterInteractMs ?? r.pINPms}ms > 200ms (approx)`);
  }
  lines.push("", over.length ? `**Over budget (baseline):**\n${over.map((o) => `- ${o}`).join("\n")}` : "**Over budget (baseline):** none of the measured budget routes.");
  writeFileSync(OUT_MD, lines.join("\n") + "\n");
  log("wrote", OUT_JSON, "and", OUT_MD);
} finally {
  if (browser) await browser.close();
  next.kill("SIGTERM");
}

/**
 * Local visual verification harness for R4 — does NOT touch RC containers.
 *
 * Serves `next start` (production build) on :3100 and drives it with Playwright
 * while proxying every API origin to the RC backends via route interception.
 * Playwright fulfils those requests from Node, so the browser's CORS check
 * never sees a cross-origin rejection. This exists because RC CORS allows
 * exactly http://localhost:3011 and the owner must approve rebuilds; it is
 * verification scaffolding, not a product feature.
 *
 * Run from section7/: node scripts/r4-check-check.mjs
 */
import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { spawn } from "node:child_process";

const PORT = 3100;
const BASE = `http://127.0.0.1:${PORT}`;
const ORIGIN = `http://localhost:${PORT}`;

const PORT_MAP = { 8001: 18001, 8002: 18002, 8003: 28003, 8004: 18004, 8005: 18005, 8006: 18006, 8008: 18008 };

function log(...a) { console.log("[r4-check]", ...a); }

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

  const email = `r4-check-${Date.now().toString(36)}@harness.dev`;
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
        await page.screenshot({ path: `../docs/briefs/evidence/ui/r4-${name}-${w}-${theme}.png`, fullPage: w === 375 });
      }
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  };

  // The seeded demo run has a pull request; everything below uses it. No model calls.
  const runUrl = `${ORIGIN}/projects/${seed.projectId}/runs/${seed.runId}?pane=diff`;
  await page.goto(runUrl);
  await page.getByTestId("review-decide").waitFor({ timeout: 30000 });
  const risky = await page.getByTestId("review-risks").count();
  log(`demo PR risk lines on screen: ${risky}`);
  await shot("review");

  // 1. Per-file reviewed tick hides that file's diff and counts it.
  const firstTick = page.locator(".ws-diff-reviewed input").first();
  const before = await page.locator(".ws-diff-code").count();
  await firstTick.focus();
  await page.keyboard.press("Space");
  check(await page.locator(".ws-diff-code").count() === before - 1, "ticking Reviewed hides that file's diff");
  check((await page.getByTestId("diff-reviewed-count").innerText()).startsWith("1 of"), "the reviewed count reads 1 of n");
  await page.keyboard.press("Space");

  // 2. Comment on a file: recorded on the review; the run is finished, so the pane says it was not sent.
  const details = page.locator(".ws-diff-comment").first();
  await details.locator("summary").focus();
  await page.keyboard.press("Enter");
  await details.locator("input[type=number]").fill("3");
  await details.locator("textarea").fill("r4-check: name this constant");
  await details.locator("textarea").focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  // The RC review service answers 500 after saving the comment until it is rebuilt with the
  // audit-allowlist fix (section1 audit.py), so both outcomes are reported, not assumed.
  const outcome = await Promise.race([
    page.getByTestId("review-comments").waitFor({ timeout: 20000 }).then(() => "listed"),
    page.locator(".ws-decide [role=alert]").waitFor({ timeout: 20000 }).then(() => "error"),
  ]);
  if (outcome === "listed") {
    check((await page.locator(".ws-decide [role=status]").last().innerText()).includes("not sent to the agent"), "a finished run says the comment was not sent to the agent");
  } else {
    log(`NOTE comment POST failed on this stack: "${await page.locator(".ws-decide [role=alert]").innerText()}"`);
    check((await details.locator("textarea").inputValue()).includes("r4-check"), "a failed comment keeps its text in the form and shows the error");
  }
  await page.reload();
  await page.getByTestId("review-comments").waitFor({ timeout: 20000 });
  check((await page.getByTestId("review-comments").innerText()).includes("r4-check: name this constant"), "the comment survives a reload (read back from the review service)");
  await shot("review-commented");

  // 3. Request changes needs a note.
  check(await page.getByTestId("review-request-changes").isDisabled(), "Request changes is disabled without a note");

  // 4. Negative test: a risky pull request cannot be approved until the box is ticked.
  //    The risk flag is injected into the review response for this check only.
  await page.route(/\/api\/v1\/reviews\/[^/]+$/, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const url = new URL(route.request().url());
    const resp = await route.fetch({ url: `http://127.0.0.1:18008${url.pathname}` });
    const json = await resp.json();
    json.riskFlags = [{ path: "auth/session.ts", severity: "high", rule: "auth-touch", reason: "touches auth" }];
    await route.fulfill({ response: resp, json });
  });
  let approveCalls = 0;
  page.on("request", (r) => { if (r.method() === "POST" && /\/approve$/.test(r.url())) approveCalls++; });
  await page.goto(runUrl);
  await page.getByTestId("review-risks").waitFor({ timeout: 30000 });
  check(await page.getByTestId("review-approve").isDisabled(), "risky PR: Approve is disabled before the tick");
  check((await page.getByTestId("review-blocked").innerText()).includes("risk flags"), "risky PR: the reason is on screen");
  await page.getByTestId("review-approve").click({ force: true }).catch(() => {});
  await page.waitForTimeout(500);
  check(approveCalls === 0, "risky PR: no approve request leaves the browser before the tick");
  await shot("review-risky");

  // 5. Keyboard only: tick, then approve.
  await page.getByTestId("review-ack").focus();
  await page.keyboard.press("Space");
  await page.getByTestId("review-approve").focus();
  await page.keyboard.press("Enter");
  await page.getByText("Approved. Merging still happens on GitHub").waitFor({ timeout: 20000 });
  check(approveCalls === 1, "keyboard-only approval sent exactly one approve request");
  await shot("review-approved");

  log("failures:", failures.length ? JSON.stringify(failures, null, 1) : "none");
  log("console errors:", consoleErrors.length ? JSON.stringify([...new Set(consoleErrors.map((e) => e.replace(/ticket=[^&]+/, "ticket=…")))], null, 1) : "none");
  await browser.close();
} finally {
  next.kill("SIGTERM");
}
log("done");

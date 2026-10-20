// browser-check.mjs: real-browser responsive, keyboard and automated-accessibility check (parts 12 and 56).
//
// Drives the running Next.js app in Chromium at four viewport widths and reports, per page and width:
//   * horizontal page overflow (WCAG 1.4.10 reflow at 320 px),
//   * the keyboard walk: skip link first and working, every tab stop has an accessible name and a visible focus
//     indicator (pixel comparison, not computed style), no keyboard trap, one keyboard-only action end to end,
//   * an axe-core scan (only when axe-core can be resolved; otherwise reported as "not run"),
//   * interactive elements smaller than 24 x 24 CSS px (WCAG 2.5.8; informational).
//
// The backend is NOT started: every API call the pages make is answered by fixtures below, so the data is synthetic and the
// result says nothing about server behaviour. It is a check of layout and keyboard behaviour only. It is not a screen-reader
// check and not a visual-regression check.
//
//   cd section7 && npx next dev -p 3111 &      # or `next build && next start -p 3111`
//   BASE_URL=http://localhost:3111 node scripts/browser-check.mjs [--json out.json]
//
// Env: BASE_URL (default http://localhost:3111), PW_CHROMIUM (path to a chromium binary; default: Playwright's own),
//      AXE_PATH (path to axe.min.js; default: resolve "axe-core" from section7/node_modules if present).
// Exit code: 0 when every hard check passes, 1 otherwise. axe findings and small targets are reported, never counted as hard.
import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = (process.env.BASE_URL ?? "http://localhost:3111").replace(/\/$/, "");
const require = createRequire(import.meta.url);
const jsonOut = process.argv.includes("--json") ? process.argv[process.argv.indexOf("--json") + 1] : null;

const VIEWPORTS = [
  { name: "320x640", width: 320, height: 640 },
  { name: "375x667", width: 375, height: 667 },
  { name: "768x1024", width: 768, height: 1024 },
  { name: "1280x800", width: 1280, height: 800 },
];
const MAX_TABS = 400;

function axeSource() {
  try {
    return readFileSync(process.env.AXE_PATH ?? require.resolve("axe-core/axe.min.js"), "utf8");
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- fixtures (synthetic; shapes follow the TS DTOs)
const NOW = "2026-10-20T09:00:00Z";
const RUN = "r1";
const ME = "lead";
const O = `/api/v1/orchestrator/runs/${RUN}`;
const json = (body, status = 200) => ({ status, body });
const FIX = {
  "GET /api/v1/projects": json({ items: [{ id: "p1", name: "Demo project", repoFullName: null }], nextCursor: 0 }),
  "GET /api/v1/notifications/unread-count": json({ count: 2 }),
  [`GET ${O}`]: json({
    runId: RUN, state: "awaiting_plan_approval", planId: "pl1", planApproved: false, isHumanEditing: false, executing: false,
    engineMode: "dry", costUsd: 0.4375, prId: null, url: null, budget: { degraded: false },
    steps: [1, 2, 3, 4].map((seq) => ({ seq, type: seq === 1 ? "plan_proposed" : "agent_progress", ts: NOW })),
  }),
  [`GET ${O}/participants`]: json({
    participants: [{ userId: ME, role: "leader" }, { userId: "eng2", role: "participant" }, { userId: "view", role: "observer" }],
    leader: ME, pendingHandoff: null,
  }),
  [`GET ${O}/approvals`]: json({
    approvals: [{
      id: "a1", runId: RUN, action: "modify", paths: ["src/app.py", "src/util.py"], summary: "Change the retry budget in two files",
      diff: "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-RETRIES = 1\n+RETRIES = 3\n", digest: "d".repeat(64), state: "pending",
      requester: "eng2", decidedBy: null, outcome: null, expiresAt: "2026-10-21T09:00:00Z",
    }],
  }),
  [`GET ${O}/presence`]: json({
    mode: "full", notice: "",
    participants: [
      { userId: ME, role: "leader", isLeader: true, self: true, presence: { status: "online" }, announcement: "lead is online" },
      { userId: "eng2", role: "participant", isLeader: false, self: false, presence: { status: "idle" }, announcement: "eng2 is idle" },
    ],
  }),
  [`GET ${O}/control`]: json({
    holder: { userId: ME, untimed: true, expiresAt: null, expiresInSeconds: null, connected: true, takeable: false },
    queue: [{ requestId: "q1", userId: "eng2", requestedAt: NOW, position: 1 }],
    queuedInstructions: [{ id: "i1", author: "eng2", content: "Please also add a test", queuedAt: NOW, expiresAt: null }],
    viewer: { userId: ME, isHolder: true, canAcquire: false, canRequest: false, canSuggest: true, queuePosition: null },
    how: { request: `POST ${O}/control/request`, acquire: `POST ${O}/control/acquire`, suggest: `POST ${O}/control/suggest` },
  }),
  [`GET ${O}/intent/conflicts`]: json({
    currentApproved: { version: 2 }, openApprovals: [],
    conflicts: [{
      id: "c1", kind: "conflict", author: "eng2", state: "open", baseVersion: 1, stale: false,
      fields: [{ field: "goal", base: "Fix retries", current: "Fix retries and logging", proposed: "Fix retries only", conflicting: true }],
    }],
  }),
  [`GET ${O}/agents`]: json({
    items: [{
      id: "ag1", parentId: null, agentName: "implementer", agentVersion: 1, title: "Implement the retry change", state: "running",
      pauseReason: null, depth: 0, budget: { allocatedUsd: "1.000000", remainingUsd: "0.562500", spentUsd: "0.437500" },
      progress: { percent: 40, note: "Editing src/app.py" },
      recentToolCalls: [{ tool: "edit_file", summary: "src/app.py", outcome: "ok", createdAt: NOW }],
    }],
  }),
  // A ticket is minted before the socket opens; nothing answers the socket, so the page falls back to polling history.
  ["POST /api/v1/ws-ticket"]: json({ ticket: "tk-1" }),
  [`GET ${O}/collab-history`]: json({
    hasMore: false, nextCursor: null, headSeq: 60,
    events: Array.from({ length: 60 }, (_, i) => ({
      seq: i + 1, actor: i % 2 ? "eng2" : ME, kind: "redirect.guidance", payload: { content: `Note number ${i + 1}` }, ts: NOW, schemaVersion: 1, runId: RUN,
    })),
  }),
  [`GET ${O}/comments`]: json({
    scope: { kind: "run", id: RUN }, nextCursor: null,
    policy: { editWindowSeconds: 900, retentionDays: 90, auditSensitive: "none", maxReplyDepth: 3, maxBodyChars: 4000, reactions: ["+1", "eyes"], refKinds: ["event"] },
    comments: [{
      id: "cm1", scopeKind: "run", scopeId: RUN, parentId: null, depth: 0, author: { userId: "eng2", name: "Eng Two" },
      body: "Should the retry budget be configurable?", state: "visible", purged: false, createdAt: NOW, editedAt: null, version: 1, edited: false,
      ref: null, reactions: { "+1": { count: 1, mine: false } }, mentions: [], moderation: null, deleted: null, promotion: null, editableUntil: null,
      can: { edit: false, delete: false, react: true, reply: true, promote: true, moderate: true },
    }],
  }),
};
// Everything else the page asks for is answered 404 so it takes its documented "not available" path.

// ---------------------------------------------------------------- helpers
async function installMocks(ctx, calls) {
  await ctx.addInitScript(([me]) => {
    localStorage.setItem("harness.session", JSON.stringify({ token: "t", principal: { userId: me, teamId: "team-a", role: "engineer" } }));
  }, [ME]);
  await ctx.route(/localhost:(8001|8004|8005|8006|8008)\//, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" };
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    const key = `${req.method()} ${url.pathname}`;
    calls.push(key);
    let hit = FIX[key];
    if (!hit && req.method() === "POST" && url.pathname === `${O}/redirect`) hit = json({ seq: 9, duplicate: false, invalidatedApprovals: 0 });
    if (!hit) hit = json({ error: { code: "NOT_FOUND", message: "no fixture" } }, 404);
    return route.fulfill({ status: hit.status, headers: { ...cors, "content-type": "application/json" }, body: JSON.stringify(hit.body) });
  });
}

const NAME_FN = () => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const text = (n) => (n?.textContent ?? "").replace(/\s+/g, " ").trim();
  const labelledby = (el.getAttribute("aria-labelledby") ?? "").split(/\s+/).filter(Boolean).map((id) => text(document.getElementById(id))).join(" ");
  const labels = el.labels ? [...el.labels].map(text).join(" ") : "";
  const name = el.getAttribute("aria-label") || labelledby || labels || text(el) || el.getAttribute("alt") || el.getAttribute("title")
    || el.getAttribute("placeholder") || (el.tagName === "INPUT" && ["button", "submit"].includes(el.type) ? el.value : "") || "";
  const r = el.getBoundingClientRect();
  const label = `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${el.getAttribute("role") ? `[role=${el.getAttribute("role")}]` : ""}`;
  return { label, name: name.trim(), x: r.x, y: r.y, w: r.width, h: r.height, tabindex: el.getAttribute("tabindex"), inMain: !!el.closest("main,[role=main]") };
};

async function focusIndicatorVisible(page, box) {
  // Compare the element's neighbourhood with and without focus. Works for outlines, box-shadows, borders and background changes.
  const pad = 6;
  const vp = page.viewportSize();
  const x = Math.max(0, Math.floor(box.x - pad)), y = Math.max(0, Math.floor(box.y - pad));
  const w = Math.min(vp.width - x, Math.ceil(box.w + pad * 2)), h = Math.min(vp.height - y, Math.ceil(box.h + pad * 2));
  if (w <= 0 || h <= 0 || box.y + box.h < 0 || box.y > vp.height) return null; // not on screen: cannot judge
  const clip = { x, y, width: w, height: h };
  const focused = await page.screenshot({ clip });
  await page.evaluate(() => { window.__prevFocus = document.activeElement; document.activeElement.blur(); });
  const blurred = await page.screenshot({ clip });
  await page.evaluate(() => { window.__prevFocus?.focus(); });
  return !focused.equals(blurred);
}

async function overflow(page) {
  return page.evaluate(() => {
    const w = window.innerWidth;
    const sw = document.documentElement.scrollWidth;
    // An element inside its own horizontal scroller (overflow auto/scroll/hidden/clip) cannot widen the page.
    const clipped = (e) => {
      for (let a = e.parentElement; a && a !== document.documentElement; a = a.parentElement) {
        const o = getComputedStyle(a).overflowX;
        if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return true;
      }
      return false;
    };
    const wide = sw > w + 1
      ? [...document.querySelectorAll("body *")].filter((e) => !clipped(e) && e.getBoundingClientRect().right > w + 1)
          .sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right)
          .slice(0, 6).map((e) => `${e.tagName.toLowerCase()}${e.id ? `#${e.id}` : ""}${e.getAttribute("aria-label") ? `[${e.getAttribute("aria-label")}]` : ""} right=${Math.round(e.getBoundingClientRect().right)}`)
      : [];
    return { innerWidth: w, scrollWidth: sw, overflowing: sw > w + 1, wide };
  });
}

async function keyboardWalk(page, { expectSkipLink }) {
  const out = { stops: 0, unnamed: [], noIndicator: [], notJudged: 0, skipLink: null, trap: false, cycled: false, sample: [] };
  // Start from the top of a freshly loaded document. No pointer click: a click moves the sequential-focus start point.
  await page.evaluate(() => { window.scrollTo(0, 0); });
  const seen = new Set();
  let prev = null, same = 0;
  for (let i = 0; i < MAX_TABS; i += 1) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(NAME_FN);
    if (!info) { out.cycled = true; break; } // focus left the document: the cycle is complete
    const key = `${info.label}|${info.name}|${Math.round(info.x)}|${Math.round(info.y)}`;
    if (i === 0) {
      out.skipLink = { label: info.label, name: info.name };
      // Judge the first stop's focus indicator now: the skip-link activation below moves focus.
      const vis0 = await focusIndicatorVisible(page, info);
      if (vis0 === null) out.notJudged += 1; else if (!vis0) out.noIndicator.push(`${info.label} "${info.name.slice(0, 30)}"`);
      if (!info.name) out.unnamed.push(info.label);
      out.stops += 1;
      seen.add(`${info.label}|${info.name}|${Math.round(info.x)}|${Math.round(info.y)}`);
      if (out.sample.length < 6) out.sample.push(`${info.label} "${info.name.slice(0, 40)}"`);
      if (expectSkipLink && /skip/i.test(info.name)) {
        await page.keyboard.press("Enter");
        const landed = await page.evaluate(() => {
          const a = document.activeElement;
          return { tag: a?.tagName?.toLowerCase(), id: a?.id ?? "", inMain: !!a?.closest?.("main,[role=main]"), hash: location.hash };
        });
        out.skipLink.landed = landed;
        // The target is usually a non-focusable <main>: the browser still moves the sequential-focus start point there.
        await page.keyboard.press("Tab");
        const after = await page.evaluate(NAME_FN);
        out.skipLink.nextStopInMain = !!after?.inMain;
        // `after` is the second stop: record it like any other (the loop below would otherwise skip it).
        if (after) {
          const k2 = `${after.label}|${after.name}|${Math.round(after.x)}|${Math.round(after.y)}`;
          seen.add(k2); out.stops += 1;
          if (!after.name) out.unnamed.push(after.label);
          const v2 = await focusIndicatorVisible(page, after);
          if (v2 === null) out.notJudged += 1; else if (!v2) out.noIndicator.push(`${after.label} "${after.name.slice(0, 30)}"`);
        }
      }
      continue;
    }
    if (seen.has(key) && i > 0) { out.cycled = true; break; }
    seen.add(key);
    if (prev === key) { same += 1; if (same > 2) { out.trap = true; break; } } else same = 0;
    prev = key;
    out.stops += 1;
    if (!info.name) out.unnamed.push(info.label);
    if (out.sample.length < 6) out.sample.push(`${info.label} "${info.name.slice(0, 40)}"`);
    const vis = await focusIndicatorVisible(page, info);
    if (vis === null) out.notJudged += 1; else if (!vis) out.noIndicator.push(`${info.label} "${info.name.slice(0, 30)}"`);
  }
  return out;
}

async function smallTargets(page) {
  return page.evaluate(() => {
    const sel = "a[href],button,input:not([type=hidden]),select,textarea,[role=button],[role=link],[tabindex]:not([tabindex='-1'])";
    return [...document.querySelectorAll(sel)].filter((e) => {
      const r = e.getBoundingClientRect();
      const cs = getComputedStyle(e);
      return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && (r.width < 24 || r.height < 24);
    }).slice(0, 12).map((e) => `${e.tagName.toLowerCase()} "${(e.textContent ?? "").trim().slice(0, 20)}" ${Math.round(e.getBoundingClientRect().width)}x${Math.round(e.getBoundingClientRect().height)}`);
  });
}

async function axeScan(page, source) {
  if (!source) return { ran: false, reason: "axe-core not found (set AXE_PATH or install axe-core)" };
  await page.evaluate(source);
  const r = await page.evaluate(async () => {
    const res = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } });
    return { version: window.axe.version, violations: res.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help, sample: v.nodes.slice(0, 3).map((n) => n.target.join(" ")),
      // colour-contrast: report the measured ratio so a reader can judge it without re-running axe
      contrast: v.id === "color-contrast" ? v.nodes.slice(0, 8).map((n) => { const d = n.any?.[0]?.data ?? {}; return `${n.target.join(" ").slice(0, 40)} ${d.fgColor} on ${d.bgColor} = ${d.contrastRatio} (needs ${d.expectedContrastRatio})`; }) : undefined })), passes: res.passes.length, incomplete: res.incomplete.map((v) => v.id) };
  });
  return { ran: true, ...r };
}

// ---------------------------------------------------------------- the check
const PAGES = [
  { name: "login", path: "/login", expectSkipLink: false },
  { name: "run (shared run page)", path: `/projects/p1/runs/${RUN}`, expectSkipLink: true, expectTerminal: true, settle: "[data-testid], main", action: true },
];

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined, args: ["--no-sandbox"] });
const axeSrc = axeSource();
const report = { base: BASE, browser: browser.version(), axe: axeSrc ? "present" : "absent", results: [] };
let hardFailures = 0;
const fail = (msg) => { hardFailures += 1; return `FAIL ${msg}`; };

for (const pg of PAGES) {
  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
    const calls = [];
    await installMocks(ctx, calls);
    const page = await ctx.newPage();
    const consoleErrors = [];
    page.on("pageerror", (e) => consoleErrors.push(String(e.message).slice(0, 120)));
    await page.goto(BASE + pg.path, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(800);
    const r = { page: pg.name, viewport: vp.name, notes: [] };
    r.overflow = await overflow(page);
    if (r.overflow.overflowing) r.notes.push(fail(`horizontal overflow ${r.overflow.scrollWidth} > ${r.overflow.innerWidth}: ${r.overflow.wide.join("; ")}`));
    r.keyboard = await keyboardWalk(page, pg);
    const k = r.keyboard;
    if (k.stops === 0) r.notes.push(fail("no tab stops"));
    if (k.trap) r.notes.push(fail("keyboard trap"));
    if (pg.expectSkipLink && !/skip/i.test(k.skipLink?.name ?? "")) r.notes.push(fail(`first tab stop is not a skip link: ${JSON.stringify(k.skipLink)}`));
    if (pg.expectSkipLink && k.skipLink?.landed && !(k.skipLink.nextStopInMain)) r.notes.push(fail(`skip link did not move the next tab stop into <main>: ${JSON.stringify(k.skipLink.landed)}`));
    if (k.unnamed.length) r.notes.push(fail(`tab stops without an accessible name: ${k.unnamed.slice(0, 5).join(", ")}`));
    if (k.noIndicator.length) r.notes.push(fail(`no visible focus change on: ${k.noIndicator.slice(0, 5).join(", ")}`));
    if (pg.expectTerminal) {
      r.terminalPanel = await page.getByRole("heading", { name: /Terminal/ }).isVisible().catch(() => false);
      if (!r.terminalPanel) r.notes.push(fail("the terminal panel heading is not on the run page"));
    }
    r.smallTargets = await smallTargets(page);
    r.axe = await axeScan(page, axeSrc);
    r.apiCalls = new Set(calls).size;
    r.pageErrors = consoleErrors;
    report.results.push(r);
    await ctx.close();
  }
}

// One keyboard-only action end to end on the run page: type guidance and send it, no pointer.
{
  const ctx = await browser.newContext({ viewport: { width: 375, height: 667 } });
  const calls = [];
  await installMocks(ctx, calls);
  const page = await ctx.newPage();
  await page.goto(`${BASE}/projects/p1/runs/${RUN}`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(800);
  const action = { name: "keyboard-only: send a direction to the agent", ok: false, detail: "" };
  try {
    const box = page.getByRole("textbox", { name: "Direction for the agent" });
    await box.waitFor({ timeout: 8000 });
    await box.focus();
    await page.keyboard.type("Use three retries");
    // Only the keyboard from here: Tab through the remaining fields to the send button.
    for (let i = 0; i < 8 && !action.ok; i += 1) {
      await page.keyboard.press("Tab");
      const f = await page.evaluate(() => ({ name: document.activeElement?.getAttribute("aria-label") || document.activeElement?.textContent?.trim() || "", tag: document.activeElement?.tagName?.toLowerCase() }));
      if (/reason for this change/i.test(f.name)) await page.keyboard.type("Agreed in the discussion");
      else if (/^send direction/i.test(f.name)) {
        await page.keyboard.press("Enter");
        await page.waitForTimeout(600);
        action.ok = calls.includes(`POST ${O}/redirect`);
        action.detail = `typed with the keyboard, reached "${f.name}" in ${i + 1} Tab(s), Enter pressed; POST /redirect seen: ${action.ok}`;
      }
    }
    if (!action.ok && !action.detail) action.detail = "textbox found, but \"Send direction\" was not reached within 8 tab stops";
  } catch (e) {
    action.detail = `could not find the "Direction for the agent" box: ${String(e.message).split("\n")[0]}`;
  }
  report.keyboardAction = action;
  if (!action.ok) hardFailures += 1;

  // History log: scroll away from the newest entry, then reach "Jump to latest" with the keyboard; focus must land on the newest entry.
  const jump = { name: "keyboard-only: history log, Jump to latest", ok: false, detail: "" };
  try {
    const log = page.getByRole("log", { name: "Collaboration history" });
    await log.waitFor({ timeout: 15000 });
    await page.waitForFunction(() => document.querySelectorAll('[role="log"] li').length >= 60, null, { timeout: 15000 });
    await log.evaluate((el) => { el.scrollTop = 0; });
    const btn = page.getByRole("button", { name: /^Jump to latest/ });
    await btn.waitFor({ timeout: 5000 });
    await btn.focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(400);
    const where = await page.evaluate(() => {
      const a = document.activeElement; const items = [...document.querySelectorAll('[role="log"] li')];
      return { tag: a?.tagName?.toLowerCase(), inLog: !!a?.closest?.('[role="log"]'), isLast: a === items[items.length - 1], text: (a?.textContent ?? "").slice(-30) };
    });
    jump.ok = where.tag === "li" && where.inLog && where.isLast;
    jump.detail = `after Enter on "Jump to latest": focus is on ${where.tag}${where.isLast ? " (the newest entry)" : ""}, in log: ${where.inLog}`;
  } catch (e) {
    jump.detail = `could not run: ${String(e.message).split("\n")[0]}`;
  }
  report.jumpToLatest = jump;
  if (!jump.ok) hardFailures += 1;
  await ctx.close();
}

await browser.close();

// ---------------------------------------------------------------- print
for (const r of report.results) {
  const k = r.keyboard;
  const axe = r.axe.ran ? `axe ${r.axe.violations.length} rule(s) violated` + (r.axe.violations.length ? ` [${r.axe.violations.map((v) => `${v.id}:${v.nodes}`).join(", ")}]` : "") : "axe not run";
  console.log(`${r.page.padEnd(22)} ${r.viewport.padEnd(9)} overflow=${r.overflow.overflowing ? "YES" : "no"}  stops=${k.stops}  unnamed=${k.unnamed.length}  noFocusChange=${k.noIndicator.length}  unjudged=${k.notJudged}  trap=${k.trap}${r.terminalPanel === undefined ? "" : `  terminal-panel=${r.terminalPanel ? "shown" : "MISSING"}`}  small<24px=${r.smallTargets.length}  ${axe}`);
  for (const n of r.notes) console.log(`    ${n}`);
}
const detail = report.results.find((r) => r.page.startsWith("run") && r.viewport === "375x667");
if (detail?.axe?.ran) for (const v of detail.axe.violations) console.log(`  axe ${v.id} (${v.impact}, ${v.nodes} node(s)): ${v.help}; e.g. ${v.sample.join(" | ").slice(0, 200)}`);
if (detail) console.log(`  small targets (375 px): ${detail.smallTargets.join("; ")}`);
console.log(`keyboard action: ${report.keyboardAction.ok ? "ok" : "FAIL"} (${report.keyboardAction.detail})`);
console.log(`history log: ${report.jumpToLatest.ok ? "ok" : "FAIL"} (${report.jumpToLatest.detail})`);
console.log(`hard failures: ${hardFailures}`);
if (jsonOut) writeFileSync(jsonOut, JSON.stringify(report, null, 2));
process.exit(hardFailures ? 1 : 0);

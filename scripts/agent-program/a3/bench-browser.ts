/**
 * Browser worker benchmark (tool latency only - NO model time).
 * Run: cd workers/browser && npx tsx ../../scripts/agent-program/a3/bench-browser.ts  (or vitest-node via npm run bench)
 */
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeAction, SessionManager, mintCapability } from "../../../workers/browser/src/index.js";
import { startSite } from "../../../workers/browser/test/fixtures/site.mjs";

const SECRET = "bench-secret";
const pct = (a: number[], p: number) => [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor((p / 100) * a.length))]!;

const site = await startSite();
const mgr = new SessionManager({ rootDir: mkdtempSync(join(tmpdir(), "a3-bench-")), headless: true });
const identity = { teamId: "bench", projectId: "p", runId: "r" };
const cap = () => mintCapability(SECRET, { v: 1, op: "browser", taskId: "t", ...identity, allowedOrigins: [site.origin], iat: Math.floor(Date.now() / 1000), exp: Math.floor(Date.now() / 1000) + 300, nonce: String(Math.random()) });

const t0 = performance.now();
const session = await mgr.open(identity, { allowedOrigins: [site.origin] });
const coldOpen = performance.now() - t0;
const t1 = performance.now();
const warm = await mgr.open({ ...identity, runId: "r2" }, { allowedOrigins: [site.origin] });
const warmOpen = performance.now() - t1;
const act = (s: typeof session, a: object) => executeAction(s, { ...a, capabilityToken: cap() } as never, { capabilitySecret: SECRET });

const coldNav0 = performance.now();
await act(session, { kind: "navigate", url: `${site.origin}/form` });
const coldNav = performance.now() - coldNav0;

const nav: number[] = [], click: number[] = [], type: number[] = [], obs: number[] = [];
let wrong = 0, total = 0;
for (let i = 0; i < 40; i += 1) {
  let t = performance.now();
  await act(session, { kind: "navigate", url: `${site.origin}/form` });
  nav.push(performance.now() - t);
  t = performance.now();
  const typed = await act(session, { kind: "type", target: { by: "selector", selector: "#note" }, text: `v${i}`, expect: { valueEquals: { target: { by: "selector", selector: "#note" }, equals: `v${i}` } } });
  type.push(performance.now() - t);
  t = performance.now();
  await act(session, { kind: "observe" });
  obs.push(performance.now() - t);
  t = performance.now();
  const c = await act(session, { kind: "click", target: { by: "role", role: "button", name: "Send local" }, expect: { urlContains: "/submitted" } });
  click.push(performance.now() - t);
  total += 2;
  if (typed.status !== "succeeded") wrong += 1;
  if (c.status !== "succeeded") wrong += 1;
}
const row = (n: string, a: number[]) => ({ op: n, n: a.length, p50: +pct(a, 50).toFixed(1), p95: +pct(a, 95).toFixed(1), p99: +pct(a, 99).toFixed(1) });
console.log(JSON.stringify({
  env: { node: process.version, platform: process.platform, arch: process.arch, viewport: "1280x800@1" },
  coldBrowserAndFirstSessionMs: +coldOpen.toFixed(0), warmSecondSessionMs: +warmOpen.toFixed(0), coldFirstNavigateMs: +coldNav.toFixed(0),
  ops: [row("navigate", nav), row("type+verify", type), row("observe", obs), row("click+postcondition", click)],
  accuracy: { steps: total, failedOrWrongTarget: wrong },
  note: "tool latency only; excludes model time",
}, null, 2));
await mgr.dispose();
site.close();
void warm;

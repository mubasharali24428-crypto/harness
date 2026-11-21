import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
const PORT = 3100; const ORIGIN = `http://localhost:${PORT}`;
const PORT_MAP = { 8001: 18001, 8002: 18002, 8003: 28003, 8004: 18004, 8005: 18005, 8006: 18006, 8008: 18008 };
function log(...a){console.log("[text-walk]",...a);}
async function wait(url,tries=60){for(let i=0;i<tries;i++){try{const r=await fetch(url);if(r.status<500)return;}catch{}await new Promise(r=>setTimeout(r,500));}throw new Error("no server");}
const next = spawn("npx",["next","start","-p",String(PORT)],{cwd:process.cwd(),stdio:["ignore","ignore","ignore"]});
let browser;
try {
  await wait(`http://127.0.0.1:${PORT}`);
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.route(/(localhost|127\.0\.0\.1):(8001|8002|8003|8004|8005|8006|8008)\//, async (route) => {
    const u = new URL(route.request().url());
    try { const resp = await route.fetch({ url: `http://127.0.0.1:${PORT_MAP[Number(u.port)]}${u.pathname}${u.search}` }); await route.fulfill({ response: resp, body: await resp.body() }); }
    catch { await route.fulfill({ status: 502, body: "{}", contentType: "application/json" }); }
  });
  await page.routeWebSocket(/\/api\/v1\/ws\//, (ws) => ws.close());
  const email = `textwalk-${Date.now().toString(36)}@harness.dev`;
  const login = await fetch("http://127.0.0.1:18001/api/v1/auth/login",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({email})}).then(r=>r.json());
  const seed = await fetch("http://127.0.0.1:18004/api/v1/orchestrator/demo/seed",{method:"POST",headers:{"content-type":"application/json",authorization:`Bearer ${login.token}`}}).then(r=>r.json());
  await page.addInitScript(([t,p])=>window.localStorage.setItem("harness.session",JSON.stringify({token:t,principal:p})),[login.token,login.principal??null]);
  const grab = async (name, path) => {
    await page.goto(`${ORIGIN}${path}`); await page.waitForTimeout(900);
    const text = (await page.locator("body").innerText()).replace(/\s+/g," ").trim();
    const h = await page.evaluate(() => [...document.querySelectorAll("h1,h2,h3")].slice(0,12).map(x => `${x.tagName}:${(x.textContent??"").trim().slice(0,60)}`));
    log(`\n##### ${name} (${path})\nTEXT: ${text.slice(0,1200)}\nHEADINGS: ${h.join(" | ")}`);
  };
  await grab("landing","/");
  await grab("settings","/settings");
  await grab("settings-team","/settings/team");
  await grab("onboarding","/onboarding");
  await grab("meeting","/meeting");
  await grab("docs","/docs");
  await grab("portal","/portal");
  await grab("triage","/triage");
  await grab("notifications","/notifications");
  await grab("run-evidence", `/projects/${encodeURIComponent(seed.projectId)}/runs/${encodeURIComponent(seed.runId)}?pane=evidence`);
  await grab("run-plan", `/projects/${encodeURIComponent(seed.projectId)}/runs/${encodeURIComponent(seed.runId)}?pane=plan`);
  await grab("checkpoints", `/projects/${encodeURIComponent(seed.projectId)}/runs/${encodeURIComponent(seed.runId)}/checkpoints`);
} finally { if(browser) await browser.close(); next.kill("SIGTERM"); }

const { chromium } = require(require.resolve("@playwright/test", { paths: [process.cwd()] }));
const AxeBuilder = require(require.resolve("@axe-core/playwright", { paths: [process.cwd()] })).default;
const [, , EMAIL, TEAM, LABEL] = process.argv;
(async () => {
  const res = await fetch("http://127.0.0.1:18001/api/v1/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(TEAM === "-" ? { email: EMAIL } : { email: EMAIL, teamId: TEAM }) });
  const body = await res.json(); if (res.status !== 200) { console.log(LABEL, "login", res.status); process.exit(1); }
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript((s) => { if (!localStorage.getItem("harness.session")) localStorage.setItem("harness.session", s); }, JSON.stringify({ token: body.token, principal: body.principal }));
  const p = await ctx.newPage(); await p.goto("http://localhost:3011/"); await p.waitForTimeout(3000);
  const out = await p.evaluate(() => ({ focus: document.activeElement?.id, rows: [...document.querySelectorAll("section.ny li.ny-row")].map((r) => r.dataset.kind + " | " + r.querySelector(".ny-meta")?.innerText.replace(/\s+/g, " ") + " | " + r.querySelector("a")?.getAttribute("aria-label")) }));
  const axe = (await new AxeBuilder({ page: p }).include("section.ny").withTags(["wcag2a","wcag2aa","wcag21aa","wcag22aa"]).analyze()).violations.map((v) => `${v.id}(${v.nodes.length})`);
  console.log(JSON.stringify({ LABEL, role: body.principal.role, ...out, axe }, null, 1)); await b.close();
})().catch((e) => { console.error(e); process.exit(2); });

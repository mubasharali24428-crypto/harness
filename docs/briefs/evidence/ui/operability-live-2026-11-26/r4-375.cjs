const { chromium } = require(require.resolve("@playwright/test", { paths: [process.cwd()] }));
const AxeBuilder = require(require.resolve("@axe-core/playwright", { paths: [process.cwd()] })).default;
const [, , EMAIL, RUN, OUT] = process.argv; const BASE = "http://localhost:3011";
(async () => {
  const b = await chromium.launch();
  const lc = await b.newContext(); const lp = await lc.newPage();
  await lp.goto(`${BASE}/login`); await lp.getByPlaceholder("you@team.dev").fill(EMAIL);
  await lp.getByRole("button", { name: /Enter the harness/i }).click();
  await lp.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  const state = await lc.storageState(); await lc.close();
  for (const scheme of ["light", "dark"]) {
    const ctx = await b.newContext({ viewport: { width: 375, height: 812 }, colorScheme: scheme, storageState: state, isMobile: true, hasTouch: true });
    const p = await ctx.newPage();
    await p.goto(`${BASE}${RUN}?tab=changes`); await p.waitForTimeout(2500);
    const before = await p.evaluate(() => { const e = document.querySelector(".ws-pane"); const r = e?.getBoundingClientRect(); return { pane: !!e, visible: !!r && r.width > 0 && r.height > 0 }; });
    const open = p.getByRole("button", { name: /Open (the )?diff/i }).first();
    if (await open.isVisible().catch(() => false)) { await open.click(); await p.waitForTimeout(1500); }
    const after = await p.evaluate(() => { const e = document.querySelector('[data-testid="diff-scope-full-pr"]'); const r = e?.getBoundingClientRect(); const pane = e?.closest(".ws-pane"); return { scopeVisible: !!r && r.width > 0 && r.height > 0, inWsPane: !!pane, decide: !!document.querySelector('[data-testid="review-decide"]'), overflow: document.documentElement.scrollWidth - innerWidth }; });
    const v = (await new AxeBuilder({ page: p }).include(".ws-pane").withTags(["wcag2a","wcag2aa","wcag21aa","wcag22aa"]).analyze());
    console.log(JSON.stringify({ scheme, before, after, violations: v.violations.map((x) => `${x.id}(${x.nodes.length})`), passes: v.passes.length }));
    await p.screenshot({ path: `${OUT}/r4-real-rc-375-${scheme}-diff-open.png` });
    await ctx.close();
  }
  await b.close();
})().catch((e) => { console.error(e); process.exit(2); });

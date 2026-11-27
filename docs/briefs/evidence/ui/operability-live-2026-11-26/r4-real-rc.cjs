// R4 rows 6-12 on the REAL RC stack (:3011): no page.route, no stubs. Run from section7/ so
// @playwright/test and @axe-core/playwright resolve from the repo's node_modules.
// usage: node <this> <adminEmail> <runPath> <otherRunPath> <outDir>
const { chromium } = require(require.resolve("@playwright/test", { paths: [process.cwd()] }));
const AxeBuilder = require(require.resolve("@axe-core/playwright", { paths: [process.cwd()] })).default;
const [, , EMAIL, RUN, OTHER, OUT] = process.argv;
const BASE = "http://localhost:3011";
const TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];
const results = [];
const rec = (row, name, ok, note) => { results.push({ row, name, ok, note }); console.log(`${ok ? "PASS" : "FAIL"} [${row}] ${name}${note ? " — " + note : ""}`); };

async function scopeState(page) {
  return page.evaluate(() => {
    const s = document.querySelector('[data-testid="diff-scope-since-last"]');
    const f = document.querySelector('[data-testid="diff-scope-full-pr"]');
    return {
      sinceLastPressed: s?.getAttribute("aria-pressed"), sinceLastDisabled: s?.disabled ?? null,
      fullPrPressed: f?.getAttribute("aria-pressed"),
      changeReview: document.querySelectorAll('[data-testid="change-review"]').length,
      flashed: window.__crSeen === true,
      decideVisible: (() => { const d = document.querySelector('[data-testid="review-decide"]'); if (!d) return false; const r = d.getBoundingClientRect(); return r.width > 0 && r.height > 0; })(),
      note: [...document.querySelectorAll(".ws-pane p, .ws-pane [role=note]")].map((p) => p.textContent.trim()).find((t) => /snapshot|iteration/i.test(t)) ?? null,
    };
  });
}

(async () => {
  const browser = await chromium.launch();
  // sign in once through the real form, reuse the stored session for every viewport/scheme
  const login = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const lp = await login.newPage();
  await lp.goto(`${BASE}/login`);
  await lp.getByPlaceholder("you@team.dev").fill(EMAIL);
  await lp.getByRole("button", { name: /Enter the harness/i }).click();
  await lp.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  const state = await login.storageState();
  await login.close();

  for (const [w, h] of [[1440, 900], [375, 812]]) {
    for (const scheme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, storageState: state });
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      await page.addInitScript(() => {
        window.__crSeen = false;
        new MutationObserver(() => { if (document.querySelector('[data-testid="change-review"]')) window.__crSeen = true; })
          .observe(document, { childList: true, subtree: true });
      });
      await page.goto(`${BASE}${RUN}?tab=changes`);
      await page.waitForSelector('[data-testid="diff-scope-full-pr"]', { timeout: 30000 });
      await page.waitForTimeout(2500); // let the checkpoint list settle and any late history mount
      const st = await scopeState(page);
      const tag = `${w}-${scheme}`;
      if (w === 1440 && scheme === "light") {
        rec(6, "default scope is since-last", st.sinceLastPressed === "true",
          `since-last pressed=${st.sinceLastPressed} disabled=${st.sinceLastDisabled}; full-PR pressed=${st.fullPrPressed}; pane says: ${st.note}`);
        rec(8, "review-decide visible", st.decideVisible, `scope shown: ${st.sinceLastPressed === "true" ? "since-last" : "full PR"}`);
        rec(9, "no ChangeReview flash during load", !(st.flashed && st.changeReview === 0),
          `history mounted at any point=${st.flashed}; mounted now=${st.changeReview}`);
        // row 7 keyboard toggle: focus since-last and press Enter/Space
        const sl = page.getByTestId("diff-scope-since-last");
        if (st.sinceLastDisabled) {
          rec(7, "keyboard toggle full PR <-> since-last", false, "since-last button is disabled (no checkpoints), so there is nothing to toggle to");
        } else {
          await page.getByTestId("diff-scope-full-pr").focus(); await page.keyboard.press("Enter");
          const a = await scopeState(page);
          await sl.focus(); await page.keyboard.press("Enter");
          const b = await scopeState(page);
          rec(7, "keyboard toggle full PR <-> since-last", a.fullPrPressed === "true" && a.changeReview > 0 && b.sinceLastPressed === "true" && b.changeReview === 0, JSON.stringify({ a, b }));
        }
        // row 11: a revert needs since-last hunks
        const hunks = await page.locator('[data-testid^="hunk-"], .hunk input[type=checkbox]').count();
        rec(11, "hunk revert available on real data", !st.sinceLastDisabled && hunks > 0, `since-last disabled=${st.sinceLastDisabled}; revertable hunk controls=${hunks}`);
        // row 10: switch to another run, scope re-defaults
        await page.goto(`${BASE}${OTHER}?tab=changes`);
        await page.waitForSelector('[data-testid="diff-scope-full-pr"]', { timeout: 30000 });
        await page.waitForTimeout(2000);
        const o = await scopeState(page);
        rec(10, "run switch opens on since-last again", o.sinceLastPressed === "true", `other run: since-last pressed=${o.sinceLastPressed} disabled=${o.sinceLastDisabled}`);
        await page.goto(`${BASE}${RUN}?tab=changes`);
        await page.waitForSelector('[data-testid="diff-scope-full-pr"]', { timeout: 30000 });
        await page.waitForTimeout(1500);
      }
      const v = (await new AxeBuilder({ page }).include(".ws-pane").withTags(TAGS).analyze()).violations;
      rec(12, `axe clean in pane at ${tag}`, v.length === 0, v.map((x) => `${x.id}(${x.nodes.length})`).join(", ") || "0 violations");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      rec(12, `no horizontal page scroll at ${tag}`, overflow <= 0, `scrollWidth - innerWidth = ${overflow}`);
      await page.screenshot({ path: `${OUT}/r4-real-rc-${tag}.png`, fullPage: false });
      if (errors.length) rec(12, `no page errors at ${tag}`, false, errors.join(" | "));
      await ctx.close();
    }
  }
  await browser.close();
  require("fs").writeFileSync(`${OUT}/r4-real-rc.json`, JSON.stringify(results, null, 2));
})().catch((e) => { console.error(e); process.exit(2); });

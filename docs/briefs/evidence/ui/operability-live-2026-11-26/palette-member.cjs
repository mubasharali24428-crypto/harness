// Command palette as a non-admin member on RC :3011. The member signs in through the identity API with
// {email, teamId} (the login form cannot pick a team: identity 409 "Select an existing team to sign in")
// and the session is seeded into this test context's localStorage, the way the app's own saveSession does.
// usage: node <this> <memberEmail> <teamId> <label>    (run from section7/)
const { chromium } = require(require.resolve("@playwright/test", { paths: [process.cwd()] }));
const AxeBuilder = require(require.resolve("@axe-core/playwright", { paths: [process.cwd()] })).default;
const [, , EMAIL, TEAM, LABEL] = process.argv;
(async () => {
  const res = await fetch("http://127.0.0.1:18001/api/v1/auth/login", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: EMAIL, teamId: TEAM }),
  });
  const body = await res.json();
  if (res.status !== 200) { console.log(JSON.stringify({ LABEL, loginStatus: res.status })); process.exit(1); }
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript((s) => { if (!localStorage.getItem("harness.session")) localStorage.setItem("harness.session", s); },
    JSON.stringify({ token: body.token, principal: body.principal }));
  const page = await ctx.newPage();
  await page.goto("http://localhost:3011/");
  await page.waitForTimeout(2500);
  await page.keyboard.press("ControlOrMeta+k");
  await page.waitForSelector("[cmdk-root]", { timeout: 10000 });
  const groups = await page.evaluate(() => [...document.querySelectorAll("[cmdk-group]")].map((g) => ({
    heading: g.querySelector("[cmdk-group-heading]")?.textContent,
    items: [...g.querySelectorAll("[cmdk-item]")].map((i) => i.textContent.replace(/\s+/g, " ").trim()),
  })));
  const axe = (await new AxeBuilder({ page }).include("[cmdk-root]").withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()).violations;
  const text = groups.flatMap((g) => g.items).join(" | ");
  console.log(JSON.stringify({
    LABEL, role: body.principal.role, groups,
    hasDecide: /Decide pending approvals/.test(text), hasInvite: /Invite a teammate/.test(text),
    axe: axe.map((v) => `${v.id}(${v.nodes.length})`),
  }, null, 1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(2); });

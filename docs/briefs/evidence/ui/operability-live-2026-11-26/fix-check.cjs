// Live re-check of F3/F4/F5 fixes on RC :3011 (frontend vv90ui2). Run from section7/.
// usage: node <this> <adminEmail> <projectId>
const { chromium } = require(require.resolve("@playwright/test", { paths: [process.cwd()] }));
const [, , EMAIL, PROJECT] = process.argv;
const ID = "http://127.0.0.1:18001/api/v1", ORCH = "http://127.0.0.1:18004/api/v1/orchestrator";
const call = async (url, token, method = "GET", body) => {
  const r = await fetch(url, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  const t = await r.text(); return { status: r.status, body: t ? JSON.parse(t) : null };
};
const out = [];
const rec = (name, ok, note) => { out.push({ name, ok, note }); console.log(`${ok ? "PASS" : "FAIL"} ${name} — ${note}`); };
(async () => {
  const login = await call(`${ID}/auth/login`, "", "POST", { email: EMAIL });
  const { token, principal } = login.body;
  const plan = await call(`${ORCH}/plans`, token, "POST", { goal: "UI fix check: receipt and focus", projectId: PROJECT });
  const run = plan.body.runId;
  await call(`${ORCH}/runs/${run}/join`, token, "POST"); await call(`${ORCH}/runs/${run}/leader`, token, "POST");
  const ask = await call(`${ORCH}/runs/${run}/approvals`, token, "POST", { action: "modify", paths: ["src/fix_check.ts"], summary: "Fix check", diff: "-a\n+b" });
  console.log("fixture", plan.status, ask.status);

  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript((s) => { if (!localStorage.getItem("harness.session")) localStorage.setItem("harness.session", s); }, JSON.stringify({ token, principal }));
  const p = await ctx.newPage();

  // F3 + F4 approvals: keyboard decide, receipt says "you" and holds focus
  await p.goto(`http://localhost:3011/projects/${PROJECT}/runs/${run}`);
  const deny = p.getByRole("button", { name: "Deny changing src/fix_check.ts" });
  await deny.waitFor({ timeout: 20000 });
  await deny.focus(); await p.keyboard.press("Enter");
  const receipt = p.locator('[data-field="receipt"]');
  await receipt.waitFor({ timeout: 10000 });
  await p.waitForTimeout(300);
  const rText = await receipt.textContent();
  rec("F3 receipt names you", /^Denied by you on /.test(rText), rText);
  const focused = await p.evaluate(() => document.activeElement?.getAttribute("data-field") + "|" + document.activeElement?.id);
  rec("F4 focus moves to receipt", focused.startsWith("receipt|approval-"), focused);

  // F4 + F5 team: confirm takes focus, cancel returns it, forced failure is named + in row + focused
  await p.goto("http://localhost:3011/settings/team");
  const members = (await call(`${ID}/teams/${principal.teamId}/members`, token)).body.items.filter((m) => m.userId && m.userId !== principal.userId);
  const m = members[0];
  const remove = p.getByRole("button", { name: `Remove ${m.displayName}` });
  await remove.waitFor({ timeout: 20000 });
  await remove.focus(); await p.keyboard.press("Enter"); await p.waitForTimeout(300);
  let f = await p.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  rec("F4 confirm takes focus", f === `Confirm removal of ${m.displayName}`, f);
  await p.keyboard.press("Tab"); await p.keyboard.press("Enter"); await p.waitForTimeout(300); // Cancel
  f = await p.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  rec("F4 cancel returns focus to Remove", f === `Remove ${m.displayName}`, f);
  await p.route(`**/members/${m.userId}`, (r) => r.request().method() === "DELETE"
    ? r.fulfill({ status: 500, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify({ error: { code: "INTERNAL_ERROR", message: "Forced test failure (500)." } }) })
    : r.continue());
  await p.keyboard.press("Enter"); await p.waitForTimeout(300); // Remove again -> confirm focused
  await p.keyboard.press("Enter"); await p.waitForTimeout(1200); // confirm -> forced 500
  const err = await p.evaluate((uid) => { const e = document.getElementById(`row-error-${uid}`); return { text: e?.textContent, inRow: !!e?.closest("tr"), focused: document.activeElement === e }; }, m.userId);
  rec("F5 error named, in its row, focused", !!err.text && err.text.includes(m.displayName) && err.inRow && err.focused, JSON.stringify(err));
  const still = await p.getByRole("button", { name: `Remove ${m.displayName}` }).count();
  rec("F5 row kept after failure", still === 1, `Remove buttons for member: ${still}`);
  await b.close();
  require("fs").writeFileSync(__dirname + "/fix-check.json", JSON.stringify(out, null, 2));
})().catch((e) => { console.error(e); process.exit(2); });

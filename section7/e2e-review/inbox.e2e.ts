import { test, expect } from "@playwright/test";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";

const root = path.resolve(__dirname, "../..");
const evidence = path.join(root, "docs/agent-program/a4/session-4/evidence");
let py: ChildProcess;
let server: Server;
let pageBase = "";
let apiBase = "";

test.use({ viewport: { width: 375, height: 812 }, hasTouch: true });

test.beforeAll(async () => {
  mkdirSync(evidence, { recursive: true });
  execFileSync("node", [path.join(__dirname, "build.mjs")], { stdio: "inherit" });
  const dist = path.join(__dirname, ".dist");
  server = createServer((req, res) => {
    const f = req.url?.startsWith("/inbox.js") ? "inbox.js" : "inbox.html";
    res.setHeader("content-type", f.endsWith(".js") ? "text/javascript" : "text/html");
    res.end(readFileSync(path.join(dist, f)));
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  pageBase = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const s2 = path.join(root, "section2");
  py = spawn(path.join(s2, ".venv/bin/python"), [path.join(s2, "tests/serve_intake_e2e.py"), pageBase], {
    stdio: ["ignore", "pipe", "inherit"],
  });
  apiBase = await new Promise((resolve, reject) => {
    let buf = "";
    py.stdout!.on("data", (d) => {
      buf += d;
      const line = buf.split("\n").find((l) => l.startsWith("{"));
      if (line) resolve(JSON.parse(line).base);
    });
    py.on("exit", () => reject(new Error("section2 exited early")));
  });
});
test.afterAll(() => {
  py?.kill("SIGKILL");
  server?.close();
});

const url = (token: string) => `${pageBase}/inbox.html?base=${encodeURIComponent(apiBase)}&token=${token}`;

test("phone layout: no overflow, 44px targets, hostile text inert, approve records without a run", async ({ page }) => {
  let dialogs = 0;
  page.on("dialog", (d) => { dialogs++; void d.dismiss(); });
  await page.goto(url("admin-tok"));
  await expect(page.getByTestId("inbox-item")).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const approve = page.getByRole("button", { name: /^Approve Login page crashes/ });
  const box = await approve.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(await page.locator("a[href^='javascript']").count()).toBe(0);
  expect(await page.getByText("<script>alert(1)</script> add dark mode").count()).toBe(1);
  await page.screenshot({ path: path.join(evidence, "inbox-mobile-before.png") });
  await approve.tap();
  await expect(page.getByTestId("inbox-notice")).toContainText("No run was started");
  await expect(page.getByTestId("inbox-summary")).toContainText("1 waiting");
  await expect(page.locator("[data-status=approved]")).toHaveCount(1);
  await page.screenshot({ path: path.join(evidence, "inbox-mobile-after.png") });
  expect(dialogs).toBe(0);
});

test("a viewer can read the inbox but the server denies their decision (UI role gating is the shell's job)", async ({ page, request }) => {
  await page.goto(url("viewer-tok") + "&write=1");
  await expect(page.getByTestId("inbox-item").first()).toBeVisible();
  const id = (await (await request.get(`${apiBase}/api/v1/developer-workflow/intake/intakes`, { headers: { authorization: "Bearer viewer-tok" } })).json()).items[0].id;
  const denied = await request.post(`${apiBase}/api/v1/developer-workflow/intake/intakes/${id}/reject`, { headers: { authorization: "Bearer viewer-tok" } });
  expect(denied.status()).toBe(403);
});

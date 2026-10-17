// Launches the REAL built Electron shell (dist/main.js) against a local page and probes its isolation.
import { test, expect, _electron as electron, type ElectronApplication } from "@playwright/test";
import { createServer, type Server } from "node:http";
import path from "node:path";

let app: ElectronApplication;
let web: Server;
let other: Server;
let origin = "";
let otherOrigin = "";

const page = `<!doctype html><title>Harness shell test</title><body><h1 id=h>hello</h1>
<script>window.__inline = "ran"</script></body>`;

const listen = (s: Server) => new Promise<string>((r) => s.listen(0, "127.0.0.1", () => r(`http://127.0.0.1:${(s.address() as { port: number }).port}`)));

test.beforeAll(async () => {
  web = createServer((_q, res) => { res.setHeader("content-type", "text/html"); res.end(page); });
  other = createServer((_q, res) => { res.setHeader("content-type", "text/html"); res.end("<title>evil</title>evil"); });
  origin = await listen(web);
  otherOrigin = await listen(other);
  app = await electron.launch({
    args: [path.resolve(__dirname, "../../dist/main.js")],
    env: { ...process.env, HARNESS_WEB_URL: origin },
  });
});
test.afterAll(async () => { await app?.close(); web?.close(); other?.close(); });

test("loads the app origin and exposes only the narrow preload API", async () => {
  const w = await app.firstWindow();
  await expect(w.locator("#h")).toHaveText("hello");
  const probe = await w.evaluate(() => ({
    require: typeof (globalThis as { require?: unknown }).require,
    process: typeof (globalThis as { process?: unknown }).process,
    api: Object.keys((window as unknown as { harnessDesktop: object }).harnessDesktop).sort(),
  }));
  expect(probe).toEqual({ require: "undefined", process: "undefined", api: ["onDeepLink", "version"] });
});

test("CSP blocks inline script", async () => {
  const w = await app.firstWindow();
  expect(await w.evaluate(() => (window as unknown as { __inline?: string }).__inline)).toBeUndefined();
});

test("navigation and window.open to another origin are blocked", async () => {
  const w = await app.firstWindow();
  await w.evaluate((u) => { location.href = u; }, otherOrigin).catch(() => undefined);
  await w.waitForTimeout(500);
  expect(w.url().startsWith(origin)).toBe(true);
  const before = app.windows().length;
  await w.evaluate((u) => { window.open(u); }, otherOrigin);
  await w.waitForTimeout(500);
  expect(app.windows().length).toBe(before);
});

test("permission requests are denied", async () => {
  const w = await app.firstWindow();
  const result = await w.evaluate(() => new Promise<string>((resolve) => {
    navigator.geolocation.getCurrentPosition(() => resolve("granted"), (e) => resolve(`denied:${e.code}`));
  }));
  expect(result.startsWith("denied")).toBe(true);
});

// The packaged (unsigned, local) Harness.app launches and keeps the same isolation guarantees.
import { test, expect, _electron as electron } from "@playwright/test";
import { createServer } from "node:http";
import { existsSync } from "node:fs";
import path from "node:path";

const exe = path.resolve(__dirname, "../../dist-app/Harness-darwin-arm64/Harness.app/Contents/MacOS/Harness");

test.skip(!existsSync(exe), "run `npm run package` first (darwin-arm64 only)");

test("packaged app loads the configured origin with isolation intact", async () => {
  const web = createServer((_q, res) => { res.setHeader("content-type", "text/html"); res.end("<title>t</title><h1 id=h>packaged</h1>"); });
  await new Promise<void>((r) => web.listen(0, "127.0.0.1", r));
  const origin = `http://127.0.0.1:${(web.address() as { port: number }).port}`;
  const app = await electron.launch({ executablePath: exe, env: { ...process.env, HARNESS_WEB_URL: origin } });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("#h")).toHaveText("packaged");
    expect(await w.evaluate(() => typeof (globalThis as { require?: unknown }).require)).toBe("undefined");
    expect(await w.evaluate(() => Object.keys((window as unknown as { harnessDesktop: object }).harnessDesktop).sort())).toEqual(["onDeepLink", "version"]);
  } finally {
    await app.close();
    web.close();
  }
});

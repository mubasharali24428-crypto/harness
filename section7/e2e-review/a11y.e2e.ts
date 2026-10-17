import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createServer, type Server } from "node:http";
import { readFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const root = path.resolve(__dirname, "../..");
const evidence = path.join(root, "docs/agent-program/a4/session-5/evidence");
const axeSource = readFileSync(createRequire(__filename).resolve("axe-core/axe.min.js"), "utf8");
let server: Server;
let base = "";

test.beforeAll(async () => {
  mkdirSync(evidence, { recursive: true });
  execFileSync("node", [path.join(__dirname, "build.mjs")], { stdio: "inherit" });
  const dist = path.join(__dirname, ".dist");
  server = createServer((req, res) => {
    const name = (req.url ?? "").split("?")[0].replace("/", "");
    const f = ["a11y.js", "a11y.html"].includes(name) ? name : "a11y.html";
    res.setHeader("content-type", f.endsWith(".js") ? "text/javascript" : "text/html");
    res.end(readFileSync(path.join(dist, f)));
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}/a11y.html`;
});
test.afterAll(() => server?.close());

async function violations(page: Page) {
  await page.evaluate(axeSource);
  return page.evaluate(async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const r = await (window as any).axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"] } });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return r.violations.map((v: any) => `${v.id}: ${v.nodes.map((n: any) => n.target.join(" ")).join(", ")}`);
  });
}

for (const lang of ["en", "es", "ar"]) {
  test(`axe finds no WCAG A/AA violations (${lang}, palette closed and open)`, async ({ page }) => {
    await page.goto(`${base}?lang=${lang}`);
    await expect(page.getByTestId("task-inbox")).toBeVisible();
    expect(await violations(page)).toEqual([]);
    await page.goto(`${base}?lang=${lang}&palette=1`);
    await expect(page.getByTestId("palette")).toBeVisible();
    expect(await violations(page)).toEqual([]);
    if (lang === "ar") {
      expect(await page.evaluate(() => document.documentElement.dir)).toBe("rtl");
      await page.screenshot({ path: path.join(evidence, "a11y-ar-rtl.png") });
    }
  });
}

test("palette is fully keyboard operable: open, filter, choose, escape, focus returns", async ({ page }) => {
  await page.goto(base);
  await page.locator("#before").focus();
  await page.keyboard.press("Control+k");
  const input = page.getByRole("combobox");
  await expect(input).toBeFocused();
  await page.keyboard.type("diff");
  await expect(page.getByRole("option")).toHaveCount(1);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("last")).toHaveText("review");
  await expect(page.getByTestId("palette")).toHaveCount(0);
  await page.keyboard.press("Control+k");
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("option", { selected: true })).toHaveText(/Open review/);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("palette")).toHaveCount(0);
  await expect(page.locator("#before")).toBeFocused();
  await page.keyboard.press("Control+Shift+i"); // direct command shortcut
  await expect(page.getByTestId("last")).toHaveText("inbox");
});

test("tab stays inside the open palette (focus trap)", async ({ page }) => {
  await page.goto(`${base}?palette=1`);
  for (let i = 0; i < 4; i++) await page.keyboard.press("Tab");
  expect(await page.evaluate(() => !!document.activeElement?.closest("[data-testid=palette]"))).toBe(true);
});

test("times follow the requested zone", async ({ page }) => {
  await page.goto(`${base}?tz=Asia/Karachi`);
  await expect(page.getByTestId("when")).toContainText("11:30");
  await page.goto(`${base}?tz=America/New_York`);
  await expect(page.getByTestId("when")).toContainText(/1:30\s?AM/);
});

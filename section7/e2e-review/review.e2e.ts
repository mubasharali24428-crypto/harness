import { test, expect } from "@playwright/test";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import path from "node:path";

const root = path.resolve(__dirname, "../..");
const svcDir = path.join(root, "integrations/editors/service");
const evidence = path.join(root, "docs/agent-program/a4/session-1/evidence");
let py: ChildProcess;
let static_: Server;
let info: { base: string; changeSetId: string; file: string };
let pageBase = "";

test.beforeAll(async () => {
  mkdirSync(evidence, { recursive: true });
  execFileSync("node", [path.join(__dirname, "build.mjs")], { stdio: "inherit" });
  py = spawn(path.join(svcDir, ".venv/bin/python"), [path.join(svcDir, "tests/serve_e2e.py")], {
    stdio: ["ignore", "pipe", "inherit"],
  });
  info = await new Promise((resolve, reject) => {
    let buf = "";
    py.stdout!.on("data", (d) => {
      buf += d;
      const line = buf.split("\n").find((l) => l.startsWith("{"));
      if (line) resolve(JSON.parse(line));
    });
    py.on("exit", () => reject(new Error("service exited early")));
  });
  const dist = path.join(__dirname, ".dist");
  static_ = createServer((req, res) => {
    const f = req.url?.startsWith("/bundle.js") ? "bundle.js" : "index.html";
    res.setHeader("content-type", f.endsWith(".js") ? "text/javascript" : "text/html");
    res.end(readFileSync(path.join(dist, f)));
  });
  await new Promise<void>((r) => static_.listen(0, "127.0.0.1", r));
  pageBase = `http://127.0.0.1:${(static_.address() as { port: number }).port}`;
});
test.afterAll(() => {
  py?.kill("SIGKILL");
  static_?.close();
});

const url = (extra = "") =>
  `${pageBase}/?base=${encodeURIComponent(info.base)}&cs=${info.changeSetId}&token=tok-eng-a${extra}`;

test("keyboard-driven per-hunk review applies only accepted hunks to the real workspace", async ({ page }) => {
  await page.goto(url());
  await expect(page.getByTestId("review-summary")).toHaveText(/0 accepted, 0 rejected, 2 pending of 2 hunks/);
  await expect(page.getByTestId("review-apply")).toBeDisabled();
  await page.screenshot({ path: path.join(evidence, "before.png") });

  // keyboard only: j -> first hunk, a accept, j -> second hunk, r reject
  await page.getByRole("group", { name: /Hunk 1 of 2/ }).focus();
  await page.keyboard.press("a");
  await expect(page.getByTestId("review-summary")).toHaveText(/1 accepted, 0 rejected, 1 pending/);
  await page.keyboard.press("j");
  await expect(page.getByRole("group", { name: /Hunk 2 of 2/ })).toBeFocused();
  await page.keyboard.press("r");
  await expect(page.getByTestId("review-summary")).toHaveText(/1 accepted, 1 rejected, 0 pending/);
  await expect(page.getByRole("button", { name: "Accept" }).first()).toHaveAttribute("aria-pressed", "true");

  await expect(page.getByTestId("review-apply")).toBeEnabled();
  await page.getByTestId("review-apply").click();
  await expect(page.getByTestId("review-applied")).toContainText("1 file(s) written");
  await page.screenshot({ path: path.join(evidence, "after.png") });

  const onDisk = readFileSync(info.file, "utf8");
  expect(onDisk).toContain("TWO"); // accepted hunk applied
  expect(onDisk).toContain("line 38"); // rejected hunk left intact
  expect(onDisk).not.toContain("THIRTY-EIGHT");
  await expect(page.getByTestId("review-apply")).toBeDisabled(); // applied change sets cannot re-apply
  expect(existsSync(info.file)).toBe(true);
});

test("read-only role: every decision control is disabled and the reason is shown", async ({ page }) => {
  // server-side denial for read-only roles is covered by the service pytest suite
  await page.goto(url("&write=0"));
  await expect(page.getByTestId("review-blocker")).toContainText(/read-only/);
  for (const b of await page.getByRole("button", { name: /Accept|Reject/ }).all()) await expect(b).toBeDisabled();
});

test("backend denial is shown, never reported as success", async ({ page }) => {
  await page.goto(`${pageBase}/?base=${encodeURIComponent(info.base)}&cs=${info.changeSetId}&token=bad-token`);
  await expect(page.getByTestId("review-error")).toContainText(/UNAUTHORIZED|invalid/i);
  await expect(page.getByTestId("review-applied")).toHaveCount(0);
});

test("service unreachable is reported honestly", async ({ page }) => {
  await page.goto(`${pageBase}/?base=${encodeURIComponent("http://127.0.0.1:1")}&cs=x&token=t`);
  await expect(page.getByTestId("review-error")).toContainText(/unreachable/i);
});

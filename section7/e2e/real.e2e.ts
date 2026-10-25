import { expect, test } from "@playwright/test";
import { readOtpFromLog } from "./support";

// Real sign-in (HARNESS_REQUIRE_OWNERSHIP=true, OTP form) and the real engine on a free model.
// Only the OTP *delivery* is dev: the code is read from the identity service's own log.
test("real OTP sign-in, then a real-model run completes", async ({ page }) => {
  const faults: string[] = [];
  page.on("pageerror", (e) => faults.push(e.message));
  // Resource-load console errors are covered by the response listener below, which names the URL.
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) faults.push(m.text());
  });
  page.on("response", (r) => {
    const path = new URL(r.url()).pathname;
    // Expected: the first /auth/login is refused with OWNERSHIP_REQUIRED, which sends the form to OTP.
    if (r.status() >= 400 && !(r.status() === 403 && path === "/api/v1/auth/login")) {
      faults.push(`${r.status()} ${path}`);
    }
  });
  page.on("requestfailed", (r) => {
    if (r.failure()?.errorText !== "net::ERR_ABORTED") faults.push(`${new URL(r.url()).pathname}: ${r.failure()?.errorText}`);
  });
  const email = "pilot-a@harness.test";
  const log = process.env.E2E_IDENTITY_LOG ?? "";
  expect(log).not.toBe("");
  await page.goto("/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Enter the harness" }).click();
  const otp = page.getByPlaceholder("123456");
  await expect(otp).toBeVisible(); // the bypass is off: the server demanded inbox proof
  await otp.fill(await readOtpFromLog(log, email));
  await page.getByRole("button", { name: /Verify code/i }).click();
  await expect(page.getByRole("heading", { name: /Command Center/ })).toBeVisible();

  await page.getByPlaceholder("Describe the goal for this run…").fill(
    "Create a file named engine_ok.txt containing exactly 'engine ok'.");
  await page.getByRole("button", { name: "Propose plan" }).click();
  await page.getByRole("button", { name: "Approve plan", exact: true }).click();
  const link = page.getByRole("link", { name: "Open shared run" });
  await link.click();
  await page.getByRole("tab", { name: "Team" }).click();
  await page.getByRole("button", { name: "Join this run" }).click();
  await page.getByRole("button", { name: "Become session leader" }).click();
  await page.getByRole("tab", { name: "Plan & work" }).click();
  await page.getByRole("button", { name: "Start run", exact: true }).click();
  await page.getByRole("tab", { name: "Working" }).click();
  await expect(page.getByTestId("run-state")).toContainText(/done|stopped/i, { timeout: 240_000 });
  expect(faults).toEqual([]);
  await page.screenshot({ path: "test-results/real-complete.png", fullPage: true });
});

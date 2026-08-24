/**
 * M13-H3a — shared helpers for the hero-journey suite.
 *
 * Auth reality (section1/src/harness_identity/app.py):
 *   • POST /api/v1/auth/login {email} → {token, principal} when the stack runs
 *     with HARNESS_REQUIRE_OWNERSHIP=false (the documented dev bypass; the same
 *     path deploy/seed_demo.py drives).
 *   • With ownership enforced the endpoint answers 403 OWNERSHIP_REQUIRED and
 *     the real flow is /auth/request-login + /auth/verify-otp, where the
 *     6-digit code is delivered by the app's notifier — with no SMTP that is
 *     the dev printer on the identity container's stderr:
 *     "HARNESS-OTP login code for <email>: <code>" (docker logs).
 *
 * The suite drives the REAL /login UI form, which calls /auth/login and — on
 * 403 OWNERSHIP_REQUIRED — flips itself to its OTP mode automatically. We
 * never hand-edit localStorage: session state is written by the app's own
 * saveSession() flow.
 */
import type { APIRequestContext, Page } from "@playwright/test";
import { expect } from "@playwright/test";

/** Unique-per-run email so repeat runs never collide with seeded rows. */
export function journeyEmail(): string {
  return `e2e-hero-${Date.now().toString(36)}@harness.dev`;
}

/**
 * Drive the real login form. Returns "bypass" when the dev path minted a
 * token directly; throws with a precise pointer when ownership is enforced
 * (the caller then runs the OTP leg via requestLoginCode).
 */
export async function loginViaUi(page: Page, email: string): Promise<"bypass"> {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "HARNESS" })).toBeVisible();
  await page.getByPlaceholder("you@team.dev").fill(email);
  await page.getByRole("button", { name: /Enter the harness/i }).click();

  // Three honest outcomes race here:
  //   A) bypass → create-project card ("Your team has no projects yet…")
  //   B) bypass + pre-existing project → Command Center (/)
  //   C) ownership enforced → OTP code form appears (403 OWNERSHIP_REQUIRED)
  const createCard = page.getByText(/Your team has no projects yet/i);
  const otpInput = page.getByPlaceholder("123456");
  await Promise.race([
    createCard.waitFor({ state: "visible", timeout: 20_000 }),
    otpInput.waitFor({ state: "visible", timeout: 20_000 }).catch(() => {}),
    page.waitForURL(/:\d+\/$|:\d+\/?$/, { timeout: 20_000 }).catch(() => {}),
  ]);

  if ((await otpInput.isVisible().catch(() => false)) && !(await createCard.isVisible().catch(() => false))) {
    throw new Error(
      "Stack enforces inbox ownership (HARNESS_REQUIRE_OWNERSHIP=true). " +
        "The OTP leg is covered by test 'login via OTP dev-notifier capture' — " +
        "run it with E2E_IDENTITY_URL reachable for docker-log capture.",
    );
  }
  return "bypass";
}

/**
 * Request an OTP through the real endpoint and read the code back from the
 * identity service container log (dev notifier prints
 * "HARNESS-OTP login code for <email>: <code>").
 */
export async function requestLoginCode(request: APIRequestContext, email: string): Promise<string> {
  const identity = process.env.E2E_IDENTITY_URL ?? "";
  if (!identity) throw new Error("E2E_IDENTITY_URL is required for OTP capture");
  await request.fetch(`${identity}/api/v1/auth/request-login`, {
    method: "POST",
    data: JSON.stringify({ email }),
    headers: { "content-type": "application/json" },
  });

  if (process.env.E2E_OTP_CODE) return process.env.E2E_OTP_CODE;

  const container = process.env.E2E_IDENTITY_CONTAINER ?? "harness-identity-1";
  try {
    const { execFile } = await import("node:child_process");
    const promisify = await import("node:util");
    const run = promisify.promisify(execFile);
    for (let attempt = 0; attempt < 10; attempt += 1) {
      const { stdout, stderr } = await run("docker", ["logs", "--tail", "80", container]);
      const lines = `${stderr}\n${stdout}`.split("\n").reverse();
      for (const line of lines) {
        if (line.includes(`HARNESS-OTP login code for ${email}:`)) {
          const m = line.match(/code ([^:]+):\s*(\d{6})/);
          if (m) return m[2];
        }
      }
      await new Promise((r) => setTimeout(r, 1_000));
      // re-request periodically so the marker stays near the tail
      if (attempt === 4) {
        await request.fetch(`${identity}/api/v1/auth/request-login`, {
          method: "POST",
          data: JSON.stringify({ email }),
          headers: { "content-type": "application/json" },
        });
      }
    }
  } catch (err) {
    throw new Error(
      `Could not read OTP from docker logs of ${container}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  throw new Error(`No HARNESS-OTP code found in ${container} logs for ${email}`);
}

/** Complete the OTP leg entirely through the UI (code entry happens there). */
export async function completeOtpInUi(page: Page, code: string): Promise<void> {
  const otpInput = page.getByPlaceholder("123456");
  await expect(otpInput).toBeVisible();
  await otpInput.fill(code);
  await page.getByRole("button", { name: /Verify code/i }).click();
}

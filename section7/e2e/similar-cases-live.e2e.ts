/**
 * Sprint 5 / Task 35 — LIVE similarCases (G-E2E-5).
 *
 * Runs on the P1 pilot stack (fresh PG18, real identity/orchestrator, deterministic Docker
 * stand-in worker) via:
 *   section4/.venv/bin/python deploy/pilot/run_smoke.py --spec similar-cases-live.e2e.ts
 * The stand-in's P1_DENY goal emits three refused shell calls in the shell plugin's own wording.
 * Everything after that is the real product: the orchestrator's loop guard stops the run,
 * case memory records the denial in Postgres, and the second run recalls the first one.
 */
import { expect, test, type Page } from "@playwright/test";
import { liveApi, storedSession } from "./support";

const orch = `${process.env.E2E_ORCH_URL ?? "http://localhost:8104"}/api/v1/orchestrator`;
const base = process.env.E2E_BASE_URL ?? "http://localhost:3107";

async function signIn(page: Page, email: string) {
  await page.goto(`${base}/login`);
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Enter the harness" }).click();
  await expect(page.getByRole("heading", { name: "New run" })).toBeVisible({ timeout: 60_000 });
}

test("live: a second denial stop recalls the first one (similarCases >= 1)", async ({ page, request }) => {
  await signIn(page, "pilot-a@harness.test");
  const { token } = await storedSession(page);

  const deniedRun = async () => {
    const plan = await liveApi(request, `${orch}/plans`, token, "POST", { goal: "P1_DENY: read the deploy secrets", projectId: "p1-project" });
    expect(plan.status, JSON.stringify(plan.body)).toBe(201);
    const approved = await liveApi(request, `${orch}/plans/${plan.body.planId}/approve`, token, "POST", {});
    expect(approved.status, JSON.stringify(approved.body)).toBe(200);
    const runId = approved.body.runId as string;
    expect((await liveApi(request, `${orch}/runs/${runId}/start`, token, "POST")).status).toBe(200);
    // The guard stops the run and the stop records its case; poll the real lesson read.
    await expect.poll(async () => (await liveApi(request, `${orch}/runs/${runId}/learned-rule`, token)).body?.offer?.failureClass ?? null,
      { timeout: 60_000 }).toBe("denials_repeated");
    return runId;
  };

  const first = await deniedRun();
  const firstLesson = (await liveApi(request, `${orch}/runs/${first}/learned-rule`, token)).body;
  expect(firstLesson.offer.paths).toEqual(["deploy/.env"]);
  expect(firstLesson.offer.status).toBe("proposed"); // CD-02: never auto-applied
  expect(firstLesson.similarCases).toEqual([]); // nothing earlier to recall, and never itself

  const second = await deniedRun();
  const lesson = (await liveApi(request, `${orch}/runs/${second}/learned-rule`, token)).body;
  expect(lesson.similarCases.length).toBeGreaterThanOrEqual(1);
  expect(lesson.similarCases[0].runId).toBe(first);
  expect(lesson.similarCases.map((c: { runId: string }) => c.runId)).not.toContain(second);
  // The approvals list carries the same team-scoped recall (Task 24 wire contract).
  const listed = await liveApi(request, `${orch}/runs/${second}/approvals`, token);
  expect(listed.status).toBe(200);

  // The shipped run page shows the lesson and the earlier run, read-only.
  await page.goto(`${base}/projects/p1-project/runs/${second}`);
  const offer = page.getByTestId("learned-rule");
  await expect(offer).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("learned-rule-text")).toContainText("deploy/.env");
  await expect(page.getByTestId("learned-rule-similar")).toContainText(first);
  await offer.screenshot({ path: "../docs/briefs/evidence/sprint5/task-35-similar-cases.png" });
});

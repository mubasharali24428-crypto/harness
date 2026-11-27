/**
 * Sprint 5 / Task 33 — LIVE blind-approval KPI (G-X07-4).
 *
 * No page.route: a real admin and a real invited engineer on the compose stack. Collab
 * approvals are decided three ways — in the UI with the diff on screen, over the API with
 * viewed_detail=false, over the API with no signal — and the admin's real KPI fetch
 * (section8 :8008) and the dashboard tile must count them exactly:
 *   E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
 *   E2E_ORCHESTRATOR_URL=http://localhost:8004 E2E_REVIEW_URL=http://localhost:8008 \
 *   npx playwright test e2e/blind-kpi-live.e2e.ts --retries=0
 */
import { expect, test } from "@playwright/test";
import { joinByInvite, journeyEmail, liveApi, loginViaUi, storedSession } from "./support";

const identity = process.env.E2E_IDENTITY_URL ?? "http://localhost:8001";
const orch = `${process.env.E2E_ORCHESTRATOR_URL ?? "http://localhost:8004"}/api/v1/orchestrator`;
const kpiUrl = `${process.env.E2E_REVIEW_URL ?? "http://localhost:8008"}/api/v1/review-kpi/blind-approval`;

test("live: collab approvals land in the admin's blind-approval KPI", async ({ page, request }) => {
  await loginViaUi(page, journeyEmail()); // a fresh solo team; its creator is the admin
  const { token: admin, principal } = await storedSession(page);
  expect(principal.role).toBe("admin");
  const peer = await joinByInvite(request, identity, admin, principal.teamId,
    `e2e-peer-${Date.now().toString(36)}@harness.dev`, "engineer");

  const empty = await liveApi(request, kpiUrl, admin);
  expect(empty.status).toBe(200);
  expect(empty.body.overall.decisions).toBe(0); // fresh team: nothing to count yet
  expect((await liveApi(request, kpiUrl, peer.token)).status).toBe(403); // admins only

  // A shared run: the admin leads, the engineer asks; self-approval stays off.
  const plan = await liveApi(request, `${orch}/plans`, admin, "POST", { goal: "Live blind KPI check" });
  expect(plan.status, JSON.stringify(plan.body)).toBe(201);
  const runId = plan.body.runId as string;
  const projectId = (plan.body.projectId as string | null) ?? "p";
  for (const t of [admin, peer.token]) expect((await liveApi(request, `${orch}/runs/${runId}/join`, t, "POST")).status).toBe(200);
  expect((await liveApi(request, `${orch}/runs/${runId}/leader`, admin, "POST")).status).toBe(200);
  const ask = async (path: string) => {
    const r = await liveApi(request, `${orch}/runs/${runId}/approvals`, peer.token, "POST",
      { action: "modify", paths: [path], summary: `change ${path}`, diff: `-old ${path}\n+new ${path}` });
    expect(r.status, JSON.stringify(r.body)).toBe(201);
    return r.body as { id: string; digest: string };
  };
  const decide = (a: { id: string; digest: string }, body: object) =>
    liveApi(request, `${orch}/runs/${runId}/approvals/${a.id}/decision`, admin, "POST", { digest: a.digest, ...body });

  // 1. approved in the shipped UI after the diff was on screen → the client measures true
  const seen = await ask("docs/seen.md");
  await page.goto(`/projects/${encodeURIComponent(projectId)}/runs/${runId}?pane=advanced`);
  const card = page.getByTestId(`approval-${seen.id}`);
  await expect(card).toBeVisible({ timeout: 60_000 });
  await card.locator(`[data-approval-diff="${seen.id}"]`).scrollIntoViewIfNeeded();
  await card.getByRole("button", { name: "Approve this change" }).click();
  await expect(card).toContainText("Approved");
  // 2. blind: approved over the API, client says the diff was never shown
  expect((await decide(await ask("docs/blind.md"), { approve: true, viewed_detail: false })).status).toBe(200);
  // 3. unknown: approved by a client that did not measure — never counted as blind
  expect((await decide(await ask("docs/unknown.md"), { approve: true })).status).toBe(200);
  // 4. a rejection is a decision, not an approval
  expect((await decide(await ask("docs/rejected.md"), { approve: false })).status).toBe(200);

  const kpi = await liveApi(request, kpiUrl, admin);
  expect(kpi.status).toBe(200);
  const counts = { decisions: 4, approvals: 3, withSignal: 2, blind: 1, unknownSignal: 1, rate: null };
  expect(kpi.body.overall).toEqual(counts); // rate stays null below minSample (10)
  expect(kpi.body.byRendering.technical).toEqual(counts); // the admin's default rendering

  // The admin's dashboard tile renders that real response.
  await page.goto("/dashboard/cost");
  await expect(page.getByTestId("kpi-overall")).toContainText("insufficient data (2 of 10 measured approvals)", { timeout: 60_000 });
  await expect(page.getByTestId("kpi-technical")).toContainText("3");
  await page.getByTestId("kpi-overall").screenshot({ path: "../docs/briefs/evidence/sprint5/task-33-kpi-tile.png" });
});

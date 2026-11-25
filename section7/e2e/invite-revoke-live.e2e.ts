/**
 * Sprint 5 / Task 36 — LIVE invite → revoke journey (G-B09-1). Engineering evidence only:
 * P-D7 stays DEFERRED and this does not grant Gate 6 build authorization.
 *
 * No page.route. An admin and two differently privileged members (engineer, viewer) on the
 * compose stack: invites minted and one revoked in the shipped /settings/team UI; the engineer
 * redeems in the UI, the viewer's redeem races a forwarded-token and a revoked-token attempt;
 * each member can do exactly what their role allows on one shared run; then the admin removes
 * the engineer (admin API) and the engineer's live token stops working everywhere.
 *   E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
 *   E2E_ORCHESTRATOR_URL=http://localhost:8004 npx playwright test e2e/invite-revoke-live.e2e.ts --retries=0
 */
import { expect, test, type Page } from "@playwright/test";
import { journeyEmail, liveApi, loginViaUi, storedSession } from "./support";

const identity = process.env.E2E_IDENTITY_URL ?? "http://localhost:8001";
const orch = `${process.env.E2E_ORCHESTRATOR_URL ?? "http://localhost:8004"}/api/v1/orchestrator`;

async function mintInUi(page: Page, email: string, role: string): Promise<string> {
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Role", { exact: true }).selectOption(role);
  await page.getByRole("button", { name: "Create invite" }).click();
  const minted = page.getByTestId("minted-token");
  await expect(minted).toContainText(email === "" ? "" : "Share this link now.");
  const token = (await minted.locator("code").textContent())?.trim() ?? "";
  expect(token.length).toBeGreaterThan(16);
  return token;
}

test("live B-09: invite engineer + viewer, revoke a third invite, remove the engineer", async ({ page, browser, request }) => {
  const stamp = Date.now().toString(36);
  const eng = `e2e-eng-${stamp}@harness.dev`;
  const viewer = `e2e-view-${stamp}@harness.dev`;
  const ghost = `e2e-ghost-${stamp}@harness.dev`;
  await loginViaUi(page, journeyEmail());
  const { token: admin, principal } = await storedSession(page);
  const teamId = principal.teamId;

  // 1. the admin mints three invites in the shipped UI and revokes the third one there
  await page.goto("/settings/team");
  const engInvite = await mintInUi(page, eng, "engineer");
  const viewInvite = await mintInUi(page, viewer, "viewer");
  const ghostInvite = await mintInUi(page, ghost, "engineer");
  await page.getByRole("button", { name: `Revoke invite for ${ghost}` }).click();
  await expect.poll(async () => ((await liveApi(request, `${identity}/api/v1/teams/${teamId}/invites`, admin)).body.items as
    { email: string; revokedAt: string | null }[]).find((i) => i.email === ghost)?.revokedAt ?? null).not.toBeNull();

  // 2. the engineer redeems through the shipped UI in their own browser session
  const engCtx = await browser.newContext();
  const engPage = await engCtx.newPage();
  await loginViaUi(engPage, eng);
  const engSolo = await storedSession(engPage);
  await engPage.goto("/settings/team");
  await engPage.getByLabel("Token", { exact: true }).fill(engInvite);
  await engPage.getByLabel("Invited email").fill(eng);
  await engPage.getByRole("button", { name: "Redeem" }).click();
  await expect(engPage.getByText(`Joined team ${teamId} as engineer.`)).toBeVisible();
  // the viewer and a forwarded-token attacker hit the API at the same moment
  const viewerSolo = await liveApi(request, `${identity}/api/v1/auth/login`, "", "POST", { email: viewer });
  const [viewRedeem, forwarded, ghostRedeem] = await Promise.all([
    liveApi(request, `${identity}/api/v1/invites/redeem`, viewerSolo.body.token, "POST", { token: viewInvite, email: viewer }),
    liveApi(request, `${identity}/api/v1/invites/redeem`, engSolo.token, "POST", { token: viewInvite, email: eng }),
    liveApi(request, `${identity}/api/v1/invites/redeem`, viewerSolo.body.token, "POST", { token: ghostInvite, email: ghost }),
  ]);
  expect(viewRedeem.status, JSON.stringify(viewRedeem.body)).toBe(200);
  expect(viewRedeem.body.membership.role).toBe("viewer");
  expect(forwarded.status).toBeGreaterThanOrEqual(400); // a token is bound to the invited email
  expect(ghostRedeem.status).toBe(410);
  expect(JSON.stringify(ghostRedeem.body)).toContain("INVITE_REVOKED");
  const signIn = async (email: string) => {
    const r = await liveApi(request, `${identity}/api/v1/auth/login`, "", "POST", { email, teamId });
    expect(r.status, JSON.stringify(r.body)).toBe(200);
    return r.body as { token: string; principal: { userId: string; role: string } };
  };
  const engIn = await signIn(eng);
  const viewIn = await signIn(viewer);
  expect([engIn.principal.role, viewIn.principal.role]).toEqual(["engineer", "viewer"]);

  // 3. one shared run: each role does exactly what it is allowed to
  const plan = await liveApi(request, `${orch}/plans`, admin, "POST", { goal: "Live B-09 invite to revoke" });
  expect(plan.status, JSON.stringify(plan.body)).toBe(201);
  const runId = plan.body.runId as string;
  for (const t of [admin, engIn.token]) expect((await liveApi(request, `${orch}/runs/${runId}/join`, t, "POST")).status).toBe(200);
  expect((await liveApi(request, `${orch}/runs/${runId}/leader`, admin, "POST")).status).toBe(200);
  const ask = { action: "modify", paths: ["docs/b09.md"], summary: "B-09 change", diff: "-a\n+b" };
  const asked = await liveApi(request, `${orch}/runs/${runId}/approvals`, engIn.token, "POST", ask);
  expect(asked.status, JSON.stringify(asked.body)).toBe(201);
  expect((await liveApi(request, `${orch}/runs/${runId}/approvals`, viewIn.token)).status).toBe(200); // viewer reads
  expect((await liveApi(request, `${orch}/runs/${runId}/approvals`, viewIn.token, "POST", ask)).status).toBe(403); // viewer cannot write

  // 4. the admin removes the engineer. Through the real admin API: the shipped Remove button sends
  //    the roster's membership id where the route expects a user id and gets 404 (finding in the
  //    Task 36 evidence). The roster UI must then no longer offer the engineer.
  const removed = await liveApi(request, `${identity}/api/v1/teams/${teamId}/members/${engIn.principal.userId}`, admin, "DELETE");
  expect(removed.status, JSON.stringify(removed.body)).toBe(204);
  await page.reload();
  const name = eng.split("@")[0];
  await expect(page.getByRole("button", { name: `Remove ${viewer.split("@")[0]}` })).toBeVisible();
  await expect(page.getByRole("button", { name: `Remove ${name}` })).toHaveCount(0);

  // 5. revoked means revoked: the engineer's live token is dead everywhere, re-entry is refused,
  //    and the viewer is untouched.
  expect((await liveApi(request, `${identity}/api/v1/me`, engIn.token)).status).toBe(401);
  expect((await liveApi(request, `${orch}/runs/${runId}/approvals`, engIn.token)).status).toBe(401);
  expect((await liveApi(request, `${orch}/runs/${runId}/approvals`, engIn.token, "POST", ask)).status).toBe(401);
  expect((await liveApi(request, `${identity}/api/v1/auth/login`, "", "POST", { email: eng, teamId })).status).toBe(403);
  expect((await liveApi(request, `${identity}/api/v1/me`, viewIn.token)).status).toBe(200);
  const members = (await liveApi(request, `${identity}/api/v1/teams/${teamId}/members`, admin)).body;
  expect(JSON.stringify(members)).not.toContain(engIn.principal.userId);
  await page.screenshot({ path: "../docs/briefs/evidence/sprint5/task-36-after-revoke.png", fullPage: true });
  await engCtx.close();
});

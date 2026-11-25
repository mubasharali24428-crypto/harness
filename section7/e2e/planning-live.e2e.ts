/**
 * Sprint 5 / Task 34 — LIVE planning page (G-E2E-4). The mocked planning.e2e.ts stays as is.
 *
 * No page.route: a real admin proposes a brief in the shipped planning UI, the real planner
 * (section4 assignment_plan.propose) names only the invited team member the brief asks for and
 * leaves the rest "Unassigned: availability unknown", the admin approves the reviewed version,
 * and the member's own real `/mine` read returns only their task.
 *   E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
 *   E2E_ORCHESTRATOR_URL=http://localhost:8004 npx playwright test e2e/planning-live.e2e.ts --retries=0
 */
import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { joinByInvite, journeyEmail, liveApi, loginViaUi, storedSession } from "./support";

const identity = process.env.E2E_IDENTITY_URL ?? "http://localhost:8001";
const orch = `${process.env.E2E_ORCHESTRATOR_URL ?? "http://localhost:8004"}/api/v1/orchestrator`;

test("live: admin proposes, reviews and approves an honest plan; the member sees only their task", async ({ page, request }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  await loginViaUi(page, journeyEmail());
  const { token: admin, principal } = await storedSession(page);
  const member = await joinByInvite(request, identity, admin, principal.teamId,
    `e2e-member-${Date.now().toString(36)}@harness.dev`, "engineer");
  const project = await liveApi(request, `${identity}/api/v1/projects`, admin, "POST", { name: "Live planning" });
  expect(project.status, JSON.stringify(project.body)).toBe(201);
  const projectId = project.body.id as string;
  const plans = `${orch}/projects/${encodeURIComponent(projectId)}/assignment-plans`;

  // The admin writes the brief in the shipped form; nothing below is mocked.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/projects/${encodeURIComponent(projectId)}/planning`);
  await expect(page.getByText("No plan yet.")).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Goals (one per line)").fill("Ship the OTP hardening");
  await page.getByLabel("Acceptance criteria (one per line)").fill("Pen test signed off");
  await page.getByLabel("Tasks (JSON)").fill(JSON.stringify([
    { key: "docs", title: "Write the threat model", requiredRole: "security", preferredAssignee: member.userId },
    { key: "pen", title: "External pen test", requiredRole: "pentester", dependsOn: ["docs"] },
  ]));
  await page.getByRole("button", { name: "Propose assignments" }).click();

  // The real planner's proposal: the named member is assigned, nobody is invented for the rest.
  const v1 = page.getByTestId("version-1");
  await expect(v1).toContainText("proposed");
  await expect(page.getByTestId("assignment-1-docs")).toContainText(member.userId);
  await expect(page.getByTestId("assignment-1-docs")).toContainText("named in brief; current team member");
  await expect(page.getByTestId("assignment-1-pen")).toContainText("Unassigned: availability unknown");
  await expect(page.getByTestId("assignment-1-pen")).toContainText("pentester");
  const axe = await new AxeBuilder({ page }).include('[data-testid="version-1"]').withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(axe.violations.filter((x) => x.impact === "serious" || x.impact === "critical").map((x) => x.id)).toEqual([]);

  // C-07 on the real server: a rejection without a note is refused; the UI also blocks it.
  const noNote = await liveApi(request, `${plans}/1/review`, admin, "POST", { decision: "reject" });
  expect(noNote.status).toBe(422);
  expect(JSON.stringify(noNote.body)).toContain("NOTE_REQUIRED");
  await expect(page.getByRole("button", { name: "Reject plan" })).toBeDisabled();
  // A member is not an admin: no proposals, no reviews.
  expect((await liveApi(request, `${plans}/1/review`, member.token, "POST", { decision: "approve" })).status).toBe(403);

  await page.getByRole("button", { name: "Approve plan" }).click();
  await expect(page.getByTestId("version-2")).toContainText("approved");
  await expect(page.getByText("(older version, read-only)")).toBeVisible();
  const versions = (await liveApi(request, plans, admin)).body.versions as { version: number; state: string; parentVersion: number | null }[];
  expect(versions.map((v) => [v.version, v.state, v.parentVersion])).toEqual([[1, "proposed", null], [2, "approved", 1]]);
  // Approving again is refused: the decision is on the version, not repeatable.
  expect((await liveApi(request, `${plans}/2/review`, admin, "POST", { decision: "approve" })).status).toBe(409);

  // The member's own read: their task only, dependency status keys only.
  const mine = await liveApi(request, `${plans}/mine`, member.token);
  expect(mine.status).toBe(200);
  expect(mine.body.version).toBe(2);
  expect(mine.body.tasks.map((t: { task: string }) => t.task)).toEqual(["docs"]);
  expect(JSON.stringify(mine.body)).not.toContain("External pen test");

  expect(pageErrors).toEqual([]);
  await page.screenshot({ path: "../docs/briefs/evidence/sprint5/task-34-planning-live.png", fullPage: true });
});

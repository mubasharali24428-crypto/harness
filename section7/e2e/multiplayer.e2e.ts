import { test, expect, type Page } from "@playwright/test";
import { readOtpFromLog } from "./support";

const orch = process.env.E2E_ORCH_URL ?? "http://localhost:8104";

/** Real HTTP call to the orchestrator as the user signed in on `page` (token read from the app's own session). */
async function api(page: Page, method: string, path: string, data?: object) {
  const token = await page.evaluate(() => (JSON.parse(localStorage.getItem("harness.session") ?? "null") as { token?: string } | null)?.token ?? "");
  expect(token).not.toBe("");
  const res = await page.request.fetch(`${orch}/api/v1/orchestrator${path}`, {
    method, data, headers: { authorization: `Bearer ${token}` },
  });
  const text = await res.text();
  return { status: res.status(), body: text ? JSON.parse(text) : null };
}

// Real services on a clean database seeded by deploy/pilot/stack.py; no API mocks.
test("P1 shared pilot: create → join → redirect → handoff → approve/apply → stop", async ({ browser }) => {
  const contexts = [await browser.newContext(), await browser.newContext()];
  const [leader, peer] = await Promise.all(contexts.map(c => c.newPage()));
  const faults: string[] = [];
  const otpLog = process.env.E2E_OTP_SIGNIN === "1" ? (process.env.E2E_IDENTITY_LOG ?? "") : "";
  const otpMode = otpLog !== "";
  if (process.env.E2E_OTP_SIGNIN === "1") expect(otpMode).toBe(true);
  const base = process.env.E2E_BASE_URL ?? "http://localhost:3107";
  let offlineWindow = false; // the only place network faults are expected (reconnect leg)
  for (const page of [leader, peer]) {
    page.on("pageerror", error => faults.push(error.message));
    // Resource-load errors are covered by the response listener, which names the URL (and exempts the expected OTP 403).
    page.on("console", m => { if (!offlineWindow && m.type() === "error" && !m.text().startsWith("Failed to load resource")) faults.push(m.text()); });
    page.on("response", r => {
      const path = new URL(r.url()).pathname;
      // With ownership enforced the first /auth/login is refused (403) and the form flips to the OTP step.
      if (r.status() >= 400 && !(otpMode && r.status() === 403 && path === "/api/v1/auth/login")) faults.push(`${r.status()} ${path}`);
    });
    page.on("requestfailed", r => {
      if (!offlineWindow && r.failure()?.errorText !== "net::ERR_ABORTED") faults.push(`${new URL(r.url()).pathname}: ${r.failure()?.errorText}`);
    });
  }
  try {
    for (const [i, page] of [leader, peer].entries()) {
      await page.goto(`${base}/login`);
      await page.getByLabel("Email address").fill(`pilot-${i === 0 ? "a" : "b"}@harness.test`);
      const email = `pilot-${i === 0 ? "a" : "b"}@harness.test`;
      await page.getByRole("button", { name: "Enter the harness" }).click();
      if (otpMode) {
        const otp = page.getByPlaceholder("123456");
        await expect(otp).toBeVisible(); // bypass is off: the server demanded inbox proof
        await otp.fill(await readOtpFromLog(otpLog, email));
        await page.getByRole("button", { name: /Verify code/i }).click();
      }
      await expect(page.getByRole("heading", { name: /Command Center/ })).toBeVisible();
    }
    await leader.getByPlaceholder("Describe the goal for this run…").fill("P1_DELETE: withhold gone.txt until reviewed");
    await leader.getByRole("button", { name: "Propose plan" }).click();
    await leader.getByRole("button", { name: "Approve plan", exact: true }).click();
    const detail = leader.getByRole("link", { name: "Open shared run" });
    const href = await detail.getAttribute("href");
    expect(href).toMatch(/^\/projects\/p1-project\/runs\//);
    await detail.click();
    await peer.goto(`${base}${href}`);
    for (const page of [leader, peer]) await page.getByRole("button", { name: "Join this run" }).click();
    await leader.getByRole("button", { name: "Become session leader" }).click();
    await expect(peer.getByTestId("leader-line")).toContainText("p1-a");
    await peer.getByLabel("Direction for the agent").fill("Keep keep.txt unchanged");
    await peer.getByRole("button", { name: "Send direction" }).click();
    // A non-holder's guidance is queued, not sent: the sender is told so and the leader accepts it first.
    await expect(peer.getByTestId("direction-notice")).toContainText("queued");
    const suggestions = leader.getByRole("region", { name: "Suggestions waiting" });
    await expect(suggestions).toContainText("Keep keep.txt unchanged");
    await suggestions.getByRole("button", { name: "Accept" }).click();
    await expect(leader.getByRole("log", { name: "Collaboration history" })).toContainText("Keep keep.txt unchanged");
    await leader.getByLabel("Hand off lead to").selectOption("p1-b");
    await leader.getByRole("button", { name: "Offer handoff" }).click();
    await peer.getByTestId("handoff-offer").getByRole("button", { name: "Accept", exact: true }).click();
    await expect(leader.getByTestId("leader-line")).toContainText("p1-b");
    await peer.getByRole("button", { name: "Start run", exact: true }).click();
    await expect(peer.getByRole("button", { name: "Approve this change" })).toBeVisible({ timeout: 45000 });
    await expect(peer.getByLabel("Proposed change")).toContainText("-delete me only after approval");
    await expect(peer.getByRole("button", { name: "Done, continue" })).toBeDisabled();
    await peer.getByRole("button", { name: "Approve this change" }).click();
    await expect(peer.getByRole("button", { name: "Done, continue" })).toBeDisabled();
    await peer.getByRole("button", { name: "Apply approved change" }).click();
    await expect(peer.getByTestId("shared-run-panel")).toContainText("Done");
    await expect(peer.getByRole("button", { name: "Done, continue" })).toBeEnabled();
    await peer.getByRole("button", { name: "Stop", exact: true }).click();
    await expect(peer.getByTestId("run-state")).toContainText(/done|stopped/i);
    // The top summary must refresh on its own (it used to stay stale after stop).
    // Plain-language labels (runsApi.ts): done/completed read "Finished", stopped "Stopped by a person".
    await expect(peer.getByLabel("Run summary")).toContainText(/finished|stopped/i, { timeout: 15000 });

    // ---- Session 2 legs, on a SECOND run so they can be checked against the first run's data ----
    const run1 = href!.split("/").pop()!;
    const run1Panel = (page: Page) => page.getByTestId("shared-run-panel");
    const history = (page: Page) => page.getByRole("log", { name: "Collaboration history" });
    let run2 = "";
    let run2Href = "";
    await test.step("setup: second run (held open), both join, p1-a leads and starts it", async () => {
      const plan = await api(leader, "POST", "/plans", { goal: "P1_SLOW: keep the second run open", projectId: "p1-project" });
      expect(plan.status).toBe(201);
      const approved = await api(leader, "POST", `/plans/${plan.body.planId}/approve`, {});
      expect(approved.status).toBe(200);
      run2 = approved.body.runId;
      run2Href = `/projects/p1-project/runs/${run2}`;
      expect(run2).not.toBe(run1);
      for (const page of [leader, peer]) {
        await page.goto(`${base}${run2Href}`);
        await page.getByRole("button", { name: "Join this run" }).click();
      }
      await leader.getByRole("button", { name: "Become session leader" }).click();
      await leader.getByRole("button", { name: "Start run", exact: true }).click();
      await expect(leader.getByTestId("run-state")).toContainText(/executing/i, { timeout: 45000 });
      await expect(peer.getByTestId("leader-line")).toContainText("p1-a");
    });

    await test.step("leg (b) cross-run isolation: run 2 shows none of run 1's data (and vice versa)", async () => {
      for (const page of [leader, peer]) {
        await expect(page.getByTestId("leader-line")).toContainText("Led by p1-a");
        await expect(run1Panel(page).getByText("No high-impact requests.")).toBeVisible();
        await expect(history(page)).not.toContainText(/Keep keep\.txt|offered the lead|accepted the lead/);
        await expect(run1Panel(page)).not.toContainText("gone.txt");
        await expect(page.getByTestId("goal-line")).toContainText("P1_SLOW: keep the second run open");
        await expect(page.getByTestId("goal-line")).not.toContainText("P1_DELETE");
      }
      const run1Approvals = (await api(peer, "GET", `/runs/${run1}/approvals`)).body.approvals as { id: string; digest: string; paths: string[] }[];
      expect(run1Approvals.length).toBeGreaterThan(0);
      const run2Approvals = (await api(peer, "GET", `/runs/${run2}/approvals`)).body.approvals as { id: string }[];
      expect(run2Approvals).toEqual([]);
      // A run-1 approval id is not addressable through run 2's routes, even for run 2's leader.
      const cross = await api(leader, "POST", `/runs/${run2}/approvals/${run1Approvals[0].id}/decision`, { approve: true, digest: run1Approvals[0].digest });
      expect(cross.status).toBe(404);
      expect(JSON.stringify(run1Approvals)).toContain("gone.txt");
    });

    let approvalId = "";
    await test.step("leg (a) a changed proposal re-requires approval; the earlier approval does not carry over", async () => {
      const proposal = (diff: string) => ({ action: "modify", paths: ["docs/plan.md"], summary: "Update docs/plan.md intro", diff });
      const created = await api(peer, "POST", `/runs/${run2}/approvals`, proposal("-old intro\n+new intro v1"));
      expect(created.status).toBe(201);
      approvalId = created.body.id;
      const v1 = created.body.digest as string;
      const card = (page: Page) => page.getByTestId(`approval-${approvalId}`);
      // Pending decision names the responsible person; the non-leader cannot decide.
      await expect(leader.getByTestId(`responsible-${approvalId}`)).toContainText("Decision needed from p1-a (you)");
      await expect(peer.getByTestId(`responsible-${approvalId}`)).toContainText("Decision needed from p1-a");
      await expect(peer.getByTestId("blocked-banner")).toContainText("p1-a decides 1 pending request");
      await expect(card(peer).getByRole("button", { name: "Approve this change" })).toHaveCount(0);
      await expect(leader.getByTestId("file-docs/plan.md")).toContainText("Waiting for p1-a");
      await expect(leader.getByTestId(`digest-${approvalId}`)).toHaveText(v1.slice(0, 8));
      // Approve with the keyboard only: focus the button, press Enter; focus lands on the card, not <body>.
      const approve = card(leader).getByRole("button", { name: "Approve this change" });
      await approve.focus();
      await expect(approve).toBeFocused();
      await leader.keyboard.press("Enter");
      await expect(card(leader)).toContainText("Approved");
      await expect(card(leader)).toBeFocused();
      await expect(leader.getByTestId("file-docs/plan.md")).toContainText("Approved");
      for (const page of [leader, peer]) {
        await expect(page.getByTestId(`decision-${approvalId}`)).toContainText("p1-a approved changing docs/plan.md");
      }
      // The requester changes the proposal: approval is void and it waits for a fresh decision.
      const amended = await api(peer, "PUT", `/runs/${run2}/approvals/${approvalId}`, proposal("-old intro\n+new intro v2 (changed after approval)"));
      expect(amended.status).toBe(200);
      expect(amended.body.state).toBe("pending");
      const v2 = amended.body.digest as string;
      expect(v2).not.toBe(v1);
      await expect(card(leader)).toContainText("Waiting for p1-a");
      await expect(leader.getByTestId(`reopened-${approvalId}`)).toContainText("The earlier approval no longer applies");
      await expect(leader.getByTestId(`digest-${approvalId}`)).toHaveText(v2.slice(0, 8));
      await expect(leader.getByLabel("Proposed change")).toContainText("new intro v2 (changed after approval)");
      await expect(leader.getByTestId("file-docs/plan.md")).toContainText("Waiting for p1-a");
      await expect(leader.getByTestId(`decision-${approvalId}`)).toHaveCount(0);
      await expect(card(leader).getByRole("button", { name: "Approve this change" })).toBeVisible();
      // A decision made on the version the approver reviewed earlier is refused by the server.
      const stale = await api(leader, "POST", `/runs/${run2}/approvals/${approvalId}/decision`, { approve: true, digest: v1 });
      expect(stale.status).toBe(409);
      expect(JSON.stringify(stale.body)).toContain("DIGEST_MISMATCH");
      await card(leader).getByRole("button", { name: "Approve this change" }).click();
      await expect(card(leader)).toContainText("Approved");
      await expect(card(peer)).toContainText("Approved");
      await expect(peer.getByTestId(`digest-${approvalId}`)).toHaveText(v2.slice(0, 8));
    });

    await test.step("leg (b, reverse) run 1's page shows none of run 2's data", async () => {
      const other = await contexts[1].newPage();
      await other.goto(`${base}/projects/p1-project/runs/${run1}`);
      await expect(other.getByTestId("leader-line")).toContainText("Led by p1-b");
      await expect(run1Panel(other)).toContainText("gone.txt");
      await expect(other.getByTestId("goal-line")).toContainText("P1_DELETE: withhold gone.txt until reviewed");
      await expect(run1Panel(other)).not.toContainText("docs/plan.md");
      await expect(history(other)).not.toContainText("P1_SLOW");
      await other.close();
    });

    await test.step("leg (c) refresh and reconnect survival, mid-run", async () => {
      await expect(leader.getByTestId("run-state")).toContainText(/executing/i);
      const rows = (page: Page) => history(page).locator("li").allInnerTexts();
      await expect.poll(async () => (await rows(leader)).length).toBeGreaterThan(2);
      const before = await rows(leader);
      await Promise.all([leader.reload(), peer.reload()]);
      for (const page of [leader, peer]) {
        await expect(page.getByTestId("leader-line")).toContainText("Led by p1-a");
        const roster = page.getByRole("list", { name: "Participants" });
        await expect(roster).toContainText("p1-a");
        await expect(roster).toContainText("p1-b");
        await expect(page.getByTestId("run-state")).toContainText(/executing/i);
        await expect(page.getByTestId(`approval-${approvalId}`)).toContainText("Approved");
        await expect(page.getByTestId(`decision-${approvalId}`)).toContainText("p1-a approved changing docs/plan.md");
        await expect.poll(() => rows(page)).toEqual(before);
      }
      // Network drop on the peer: it shows an error, the leader keeps working, and the peer catches up on its own.
      offlineWindow = true;
      await contexts[1].setOffline(true);
      // With the push socket up the UI's safety poll is 20 s, so the error can take that long to show.
      await expect(peer.getByTestId("shared-run-panel").getByRole("alert")).toBeVisible({ timeout: 30000 });
      await leader.getByLabel("Direction for the agent").fill("Sent while the peer was offline");
      await leader.getByRole("button", { name: "Send direction" }).click();
      await expect(history(leader)).toContainText("Sent while the peer was offline");
      await contexts[1].setOffline(false);
      await expect(history(peer)).toContainText("Sent while the peer was offline", { timeout: 20000 });
      await expect(peer.getByTestId("shared-run-panel").getByRole("alert")).toHaveCount(0);
      await expect(peer.getByTestId("leader-line")).toContainText("Led by p1-a");
      offlineWindow = false;
      await leader.getByRole("button", { name: "Stop", exact: true }).click();
      await expect(leader.getByTestId("run-state")).toContainText(/done|stopped/i);
      await leader.screenshot({ path: "test-results/multiplayer-run2.png", fullPage: true });
    });
    expect(faults).toEqual([]);
    await peer.screenshot({ path: "test-results/multiplayer-complete.png", fullPage: true });
  } finally {
    for (const context of contexts) await context.close();
  }
});

// M12-E7 (raw-04 M1) — checklist view component tests.
// Node-env constraint (no jsdom/RTL in this repo; installs are out of scope):
// the presentational rows are rendered with react-dom/server's real renderer
// and asserted on emitted markup, same protocol as RunsTable/planGate tests.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ChecklistItemDto } from "./onboardingApi";
import { deriveItems } from "./onboardingDerived";
import { ChecklistRows, SignalsCard } from "./checklistView";

const ITEM = (
  id: string,
  title: string,
  serverDone = false,
  description: string | null = null,
): ChecklistItemDto => ({ id, title, description, done: serverDone });

const SERVER_ROWS: ChecklistItemDto[] = [
  ITEM("connect_github", "Connect GitHub"),
  ITEM("create_team", "Create a team"),
  ITEM("invite_members", "Invite members"),
  ITEM("api_key_added", "Add a provider API key"),
  ITEM("first_run", "Complete a first run"),
  ITEM("guided_demo", "Open the guided demo", true),
];

describe("ChecklistRows — per-step action links (raw-04 M1)", () => {
  it("not-done steps carry an accent link to their owning surface", () => {
    const html = renderToStaticMarkup(<ChecklistRows items={SERVER_ROWS} />);
    // Deep links to where each step actually happens...
    expect(html).toContain('href="/settings"');
    expect(html).toContain('href="/settings/team"'); // create_team + invite_members
    expect(html).toContain('href="/settings/keys"');
    expect(html).toContain('href="/"');
    // ...only on not-done steps: the completed guided_demo row gets no link.
    const guidedRow = html.split('data-testid="checklist-item-guided_demo"')[1] ?? "";
    expect(guidedRow).not.toContain("<a ");
  });

  it("keeps per-item testids and honest Done/To do pills", () => {
    const html = renderToStaticMarkup(<ChecklistRows items={SERVER_ROWS} />);
    for (const id of ["connect_github", "create_team", "api_key_added", "guided_demo"]) {
      expect(html).toContain(`data-testid="checklist-item-${id}"`);
    }
    expect(html).toContain("Done");
    expect(html).toContain("To do");
  });

  it("descriptions render when present and stay absent otherwise", () => {
    const withDesc = [
      ITEM("first_run", "Complete a first run", false, "Start one from the Command Center."),
    ];
    const html = renderToStaticMarkup(<ChecklistRows items={withDesc} />);
    expect(html).toContain("Start one from the Command Center.");
    const without = renderToStaticMarkup(
      <ChecklistRows items={[ITEM("first_run", "Complete a first run")]} />,
    );
    expect(without).not.toContain("Start one");
  });
});

describe("ChecklistRows × deriveItems — auto-complete end to end", () => {
  it("a satisfied condition renders ✅ Done with its action link removed", () => {
    // Mock API state: github connected, keys added, first run recorded; team
    // exists but only one member → invite_members stays honestly open.
    const derived = deriveItems(SERVER_ROWS, {
      teamExists: true,
      membersCount: 1,
      githubConnected: true,
      keysCount: 2,
      runsTotal: 5,
    });
    const html = renderToStaticMarkup(<ChecklistRows items={derived} />);
    expect((html.match(/✅/g) ?? []).length).toBe(5); // github, team, key, run, guided_demo
    expect(html).toContain("⬜"); // create_team? no — it IS done. invite_members is open:
    const inviteRow = html.split('data-testid="checklist-item-invite_members"')[1] ?? "";
    expect(inviteRow).toContain("To do"); // 1 member ≠ invited teammates
    expect(inviteRow).toContain('href="/settings/team"');
    // Satisfied steps drop their action links — nothing left to do there.
    const keysRow = html.split('data-testid="checklist-item-api_key_added"')[1] ?? "";
    expect(keysRow).not.toContain('href="/settings/keys"');
  });

  it("all-unknown signals fall back to server verdicts — nothing fakes progress", () => {
    const html = renderToStaticMarkup(
      <ChecklistRows items={deriveItems(SERVER_ROWS, {
        teamExists: null,
        membersCount: null,
        githubConnected: null,
        keysCount: null,
        runsTotal: null,
      })} />,
    );
    expect((html.match(/✅/g) ?? []).length).toBe(1); // only backend-owned guided_demo
  });
});

describe("SignalsCard — live-signal readouts", () => {
  it("renders known values and marks satisfied conditions", () => {
    const html = renderToStaticMarkup(
      <SignalsCard signals={{
        teamExists: true,
        membersCount: 2,
        githubConnected: true,
        keysCount: 0,
        runsTotal: 3,
      }} />,
    );
    expect(html).toContain("connected");
    expect(html).toContain("2 members");
    expect(html).toContain(">3</strong>");
    expect((html.match(/✅/g) ?? []).length).toBe(3); // github, team+members, runs — keys stays ⬜ at 0
  });

  it("unknown probes render as an explicit dash + 'unknown', never as progress", () => {
    const html = renderToStaticMarkup(
      <SignalsCard signals={{ teamExists: null, membersCount: null, githubConnected: null, keysCount: null, runsTotal: null }} />,
    );
    expect((html.match(/—/g) ?? []).length).toBeGreaterThanOrEqual(4);
    expect((html.match(/unknown/g) ?? []).length).toBeGreaterThanOrEqual(4);
    expect(html).not.toContain("✅");
  });
});

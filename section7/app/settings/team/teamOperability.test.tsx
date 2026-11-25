// Operability slice 4: team settings a person can finish. Node-env vitest (no jsdom/RTL in this
// repo): the presentational tables are expanded with tests/support/elementTree, their buttons are
// pressed, and the actions are wired to ./rosterOps exactly as page.tsx wires them, against a
// stubbed fetch. The fixture gives every member a membership row id that differs from its user id,
// so a URL built from the wrong one fails the test (D3).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { button, expand, findAll, press, textOf, type Node } from "../../../tests/support/elementTree";
import { IDENTITY_BASE } from "@/lib/api";
import type { TeamInviteDto, TeamMemberDto } from "./teamApi";
import { InvitesTable, MembersTable, type InviteActions, type RosterActions } from "./tables";
import { changeRoleOptimistic, memberByUserId, removeMemberOptimistic, restoreAt, type MembersStore } from "./rosterOps";

afterEach(() => vi.unstubAllGlobals());

const TEAM = "team-1";
const TOKEN = "tok";

const MEMBERS: TeamMemberDto[] = [
  { id: "mem-row-1", userId: "user-ada", displayName: "Ada", email: "ada@x.com", memberType: "human", role: "admin", createdAt: "2026-11-01T00:00:00Z" },
  { id: "mem-row-2", userId: "user-bo", displayName: "Bo", email: "bo@x.com", memberType: "human", role: "engineer", createdAt: "2026-11-02T00:00:00Z" },
  { id: "mem-row-3", userId: "user-cy", displayName: "Cy", email: "cy@x.com", memberType: "human", role: "viewer", createdAt: "2026-11-03T00:00:00Z" },
];
const INVITES: TeamInviteDto[] = [
  { id: "inv-live", teamId: TEAM, email: "new@x.com", role: "engineer", expiresAt: "2026-12-01T00:00:00Z", redeemedAt: null, revokedAt: null, createdAt: "2026-11-20T00:00:00Z", status: "sent" },
  { id: "inv-done", teamId: TEAM, email: "old@x.com", role: "viewer", expiresAt: "2026-11-10T00:00:00Z", redeemedAt: "2026-11-05T00:00:00Z", revokedAt: null, createdAt: "2026-11-01T00:00:00Z", status: "redeemed" },
];

const stub = (status: number, body?: unknown) =>
  vi.fn(async () => ({ ok: status >= 200 && status < 300, status, text: async () => (body === undefined ? "" : JSON.stringify(body)) }));

/** The page's roster state and glue, minus React: members, pending confirm, busy row, error. */
function rosterHarness() {
  let members = [...MEMBERS];
  let pendingRemovalId: string | null = null;
  let rosterError: string | null = null;
  const store: MembersStore = { get: () => members, set: (n) => { members = typeof n === "function" ? n(members) : n; } };
  const actions: RosterActions = {
    onAskRemove: (id) => { pendingRemovalId = id; },
    onCancelRemove: (id) => { if (pendingRemovalId === id) pendingRemovalId = null; },
    onConfirmRemove: (id) => {
      pendingRemovalId = null;
      pending = removeMemberOptimistic(store, TEAM, memberByUserId(members, id)!, TOKEN).then((r) => { if (!r.ok) rosterError = r.error; });
    },
    onRoleChange: (id, role) => {
      pending = changeRoleOptimistic(store, TEAM, memberByUserId(members, id)!, role, TOKEN).then((r) => { if (!r.ok) rosterError = r.error; });
    },
  };
  let pending: Promise<void> = Promise.resolve();
  const render = () => expand(React.createElement(MembersTable, { members, isAdmin: true, busyUserId: null, pendingRemovalId, actions }));
  return { render, actions, settle: () => pending, members: () => members, error: () => rosterError };
}

const rows = (nodes: Node[]) => findAll(nodes, (n) => n.type === "tr").slice(1);
const cells = (row: Node) => findAll([row], (n) => n.type === "td").map(textOf);

describe("member list and invites show email and role from the fixture", () => {
  it("each member row shows name, email and role", () => {
    const h = rosterHarness();
    const r = rows(h.render());
    expect(r).toHaveLength(3);
    expect(cells(r[1]).slice(0, 2)).toEqual(["Bo", "bo@x.com"]);
    const select = findAll([r[1]], (n) => n.type === "select")[0];
    expect(select.props.value).toBe("engineer");
    expect(select.props["aria-label"]).toBe("Role for Bo");
  });

  it("each invite row shows email, role and server status", () => {
    const nodes = expand(React.createElement(InvitesTable, { invites: INVITES, busyInviteId: null, actions: { onResend: () => {}, onRevoke: () => {} } }));
    const [live, done] = rows(nodes);
    expect(cells(live).slice(0, 3)).toEqual(["new@x.com", "engineer", "sent"]);
    expect(cells(done).slice(0, 3)).toEqual(["old@x.com", "viewer", "redeemed"]);
  });
});

describe("D3: remove and role change call …/members/{userId}, never the membership row id", () => {
  it("Remove → in-page confirm → DELETE /teams/team-1/members/user-bo", async () => {
    const f = stub(204);
    vi.stubGlobal("fetch", f);
    const h = rosterHarness();
    press(button(h.render(), "Remove Bo"));
    expect(f).not.toHaveBeenCalled(); // the first press only asks
    const confirmText = rows(h.render()).map((r) => textOf(r)).join(" ");
    expect(confirmText).toContain("Remove this member?");
    press(button(h.render(), "Confirm removal of Bo"));
    await h.settle();
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/teams/${TEAM}/members/user-bo`);
    expect(url).not.toContain("mem-row");
    expect(init.method).toBe("DELETE");
    expect(h.members().map((m) => m.userId)).toEqual(["user-ada", "user-cy"]);
  });

  it("role change → PATCH /teams/team-1/members/user-cy with {role}", async () => {
    const f = stub(200, { userId: "user-cy", role: "engineer" });
    vi.stubGlobal("fetch", f);
    const h = rosterHarness();
    const select = findAll(h.render(), (n) => n.type === "select" && n.props["aria-label"] === "Role for Cy")[0];
    press(select, "onChange", { target: { value: "engineer" } });
    await h.settle();
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/teams/${TEAM}/members/user-cy`);
    expect(url).not.toContain("mem-row");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ role: "engineer" });
    expect(memberByUserId(h.members(), "user-cy")?.role).toBe("engineer");
  });

  it("a row with no user id (an agent) is never sent", async () => {
    const f = stub(204);
    vi.stubGlobal("fetch", f);
    const store: MembersStore = { get: () => [], set: () => {} };
    const out = await removeMemberOptimistic(store, TEAM, { ...MEMBERS[0], userId: null }, TOKEN);
    expect(out.ok).toBe(false);
    expect(f).not.toHaveBeenCalled();
  });
});

describe("destructive actions confirm in the page; failure keeps the row and shows the error", () => {
  it("Cancel on the remove confirm sends nothing and keeps the row", () => {
    const f = stub(204);
    vi.stubGlobal("fetch", f);
    const h = rosterHarness();
    press(button(h.render(), "Remove Bo"));
    press(button(h.render(), /^Cancel$/));
    expect(f).not.toHaveBeenCalled();
    expect(findAll(h.render(), (n) => n.props["data-testid"] === "ask-remove-user-bo")).toHaveLength(1);
  });

  it("a failed DELETE puts the row back in its place and keeps the server's message", async () => {
    vi.stubGlobal("fetch", stub(409, { detail: { error: { code: "LAST_ADMIN", message: "cannot remove the last admin" } } }));
    const h = rosterHarness();
    press(button(h.render(), "Remove Ada"));
    press(button(h.render(), "Confirm removal of Ada"));
    await h.settle();
    expect(h.members().map((m) => m.userId)).toEqual(["user-ada", "user-bo", "user-cy"]);
    expect(h.error()).toBe("cannot remove the last admin");
  });

  it("a failed PATCH snaps the role back and keeps the error", async () => {
    vi.stubGlobal("fetch", stub(403, { error: { code: "forbidden", message: "admins only" } }));
    const h = rosterHarness();
    h.actions.onRoleChange("user-bo", "admin");
    await h.settle();
    expect(memberByUserId(h.members(), "user-bo")?.role).toBe("engineer");
    expect(h.error()).toBeTruthy();
  });

  it("restoreAt clamps the index and replaces a row that is still present", () => {
    expect(restoreAt(MEMBERS.slice(1), MEMBERS[0], 0).map((m) => m.id)).toEqual(["mem-row-1", "mem-row-2", "mem-row-3"]);
    expect(restoreAt([], MEMBERS[0], 9).map((m) => m.id)).toEqual(["mem-row-1"]);
    expect(restoreAt(MEMBERS, { ...MEMBERS[1], role: "viewer" }, 1).map((m) => m.role)).toEqual(["admin", "viewer", "viewer"]);
  });

  it("Revoke asks in the row first; only Yes, revoke calls the revoke action", () => {
    const calls: string[] = [];
    let pendingRevokeId: string | null = null;
    const actions: InviteActions = {
      onResend: () => {},
      onRevoke: (id) => calls.push(`revoke:${id}`),
      onAskRevoke: (id) => { pendingRevokeId = id; },
      onCancelRevoke: () => { pendingRevokeId = null; },
    };
    const render = () => expand(React.createElement(InvitesTable, { invites: INVITES, busyInviteId: null, pendingRevokeId, actions }));
    press(button(render(), "Revoke invite for new@x.com"));
    expect(calls).toEqual([]);
    expect(rows(render()).map(textOf).join(" ")).toContain("Revoke this invite?");
    press(button(render(), "Keep the invite for new@x.com"));
    expect(pendingRevokeId).toBeNull();
    press(button(render(), "Revoke invite for new@x.com"));
    press(button(render(), "Confirm revoking the invite for new@x.com"));
    expect(calls).toEqual(["revoke:inv-live"]);
  });

  it("every new control has an accessible name", () => {
    const h = rosterHarness();
    h.actions.onAskRemove("user-bo");
    const nodes = h.render();
    for (const b of findAll(nodes, (n) => n.type === "button")) {
      expect((b.props["aria-label"] ?? textOf(b)).trim()).not.toBe("");
    }
  });
});

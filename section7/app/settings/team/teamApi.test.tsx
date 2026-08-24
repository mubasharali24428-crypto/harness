// M12-E3b (H-010, pair contract W2-C1) — roster management client tests.
// Mock-fetch protocol per lane instructions: happy + error paths for
// removeMember/updateMemberRole, plus the admin-gated MembersTable rendering.
// UNVERIFIED against the live server until lane E3a lands (ATLAS diffs at gate).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError, IDENTITY_BASE } from "@/lib/api";
import { removeMember, updateMemberRole } from "./teamApi";
// M14-F4 — row-scoped optimistic rollback + components live in tables.tsx:
// Next 15's generated route-type check rejects any non-default export from a
// page module, so importing them from ./page no longer typechecks (same
// components/helpers, new module boundary).
import { MembersTable, restoreRow, type RosterActions } from "./tables";

afterEach(() => {
  vi.unstubAllGlobals();
});

const TOKEN = "tok-123";

function stubFetch(responses: Array<{ status: number; body?: unknown }>) {
  const queue = [...responses];
  return vi.fn(async () => {
    const next = queue.shift() ?? { status: 500 };
    return {
      ok: next.status >= 200 && next.status < 300,
      status: next.status,
      text: async () => (next.body === undefined ? "" : JSON.stringify(next.body)),
    };
  });
}

const noopActions: RosterActions = {
  onAskRemove: () => {},
  onCancelRemove: () => {},
  onConfirmRemove: () => {},
  onRoleChange: () => {},
};

function member(partial: { id?: string; role?: string; displayName?: string; email?: string | null }) {
  return {
    id: partial.id ?? "u2",
    displayName: partial.displayName ?? "Bob",
    email: partial.email ?? null,
    memberType: "human",
    role: partial.role ?? "engineer",
    createdAt: "2026-08-01T00:00:00Z",
  } as const;
}

describe("W2-C1 client: removeMember", () => {
  it("DELETEs /api/v1/teams/{id}/members/{userId} with bearer and resolves on 204", async () => {
    const f = stubFetch([{ status: 204 }]);
    vi.stubGlobal("fetch", f);
    await expect(removeMember("team-1", "user-7", TOKEN)).resolves.toBe(null);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/teams/team-1/members/user-7`);
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("surfaces the server's error_body message verbatim on failure", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch([
        { status: 409, body: { detail: { error: { code: "LAST_ADMIN", message: "cannot remove the last admin" } } } },
      ]),
    );
    try {
      await removeMember("team-1", "user-7", TOKEN);
      expect.fail("expected ApiError");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).message).toBe("cannot remove the last admin");
      expect((e as ApiError).status).toBe(409);
    }
  });
});

describe("W2-C1 client: updateMemberRole", () => {
  it("PATCHes {role} to .../members/{userId} and returns the echoed {userId, role}", async () => {
    const f = stubFetch([{ status: 200, body: { userId: "user-7", role: "viewer" } }]);
    vi.stubGlobal("fetch", f);
    await expect(updateMemberRole("team-1", "user-7", "viewer", TOKEN)).resolves.toEqual({
      userId: "user-7",
      role: "viewer",
    });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/teams/team-1/members/user-7`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ role: "viewer" });
  });

  it("maps a non-admin 403 error_body rejection to an honest ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch([
        { status: 403, body: { error: { code: "FORBIDDEN", message: "admin role required" } } },
      ]),
    );
    try {
      await updateMemberRole("team-1", "user-7", "admin", TOKEN);
      expect.fail("expected ApiError");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).message).toBe("admin role required");
      expect((e as ApiError).code).toBe("FORBIDDEN");
    }
  });
});

describe("MembersTable admin gating", () => {
  it("renders Remove buttons + role dropdowns for admins only", () => {
    const html = renderToStaticMarkup(
      <MembersTable
        members={[member({ id: "u2" })]}
        isAdmin
        busyUserId={null}
        pendingRemovalId={null}
        actions={noopActions}
      />,
    );
    expect(html).toContain('data-testid="ask-remove-u2"');
    expect(html).toContain('data-testid="role-select-u2"');
    // no stale pill when the dropdown takes over the Role cell
    expect(html).toContain("<select");
  });

  it("hides ALL management controls from non-admin viewers (read-only pills)", () => {
    const html = renderToStaticMarkup(
      <MembersTable
        members={[member({ id: "u2", role: "viewer" })]}
        isAdmin={false}
        busyUserId={null}
        pendingRemovalId={null}
        actions={noopActions}
      />,
    );
    expect(html).not.toContain('data-testid="ask-remove-u2"');
    expect(html).not.toContain("Remove");
    expect(html).not.toContain("<select");
    // M14-DS: role pills render via ui/Pill token styles now (inline
    // var(--yellow)) instead of the legacy .pill.yellow class — assert the
    // visible vocabulary, which both forms satisfy.
    expect(/yellow/.test(html)).toBe(true); // read-only role pill stays yellow
  });

  it("shows the inline confirm state for the pending member with confirm + cancel", () => {
    const html = renderToStaticMarkup(
      <MembersTable
        members={[member({ id: "u2" }), member({ id: "u3", displayName: "Ada" })]}
        isAdmin
        busyUserId={null}
        pendingRemovalId="u2"
        actions={noopActions}
      />,
    );
    expect(html).toContain('data-testid="confirm-remove-u2"');
    expect(html).toContain('data-testid="cancel-remove-u2"');
    expect(html).toContain("Remove this member?");
    // the other row still shows its plain Remove button
    expect(html).toContain('data-testid="ask-remove-u3"');
  });
});

// M14-F4 — row-scoped optimistic rollback (pure, headless). Each mutation
// restores ONLY the row it touched: a failed removal must not resurrect rows
// other actions deleted (zombie rows), and a failed role change must not
// clobber newer changes to other rows.
describe("restoreRow — optimistic rollback", () => {
  it("removal rollback re-inserts exactly the removed member", () => {
    const bob = member({ id: "u2" });
    const afterRemoval = [member({ id: "u1", role: "admin" })]; // u2 gone
    expect(restoreRow(afterRemoval, bob)).toEqual([
      member({ id: "u1", role: "admin" }),
      bob,
    ]);
  });

  it("role-change rollback snaps only that row back to its prior state", () => {
    const bobBefore = member({ id: "u2", role: "engineer" });
    const current = [
      member({ id: "u1", role: "admin" }),
      member({ id: "u2", role: "viewer" }), // failed optimistic change
      member({ id: "u3", displayName: "Ada", role: "guest" }), // untouched
    ];
    expect(restoreRow(current, bobBefore)).toEqual([
      member({ id: "u1", role: "admin" }),
      bobBefore,
      member({ id: "u3", displayName: "Ada", role: "guest" }),
    ]);
  });

  it("does not resurrect rows other confirmed actions deleted (no zombie rows)", () => {
    const bob = member({ id: "u2" });
    const current = [] as ReturnType<typeof member>[]; // u3 was separately
    // removed by another CONFIRMED action after u2's failed one rolled back.
    expect(restoreRow(current, bob)).toEqual([bob]); // only u2 comes back
  });
});

"use client";

// Wave 9 node C — /settings/team: roster with roles, admin invite flow
// (mint → token shown ONCE), pending-invites table with revoke, and
// join-by-token redemption. Real fetches against Section 1, no mocks.
//
// M12-E3b (H-010, pair contract W2-C1) — roster management UI:
//   • per-member Remove button with INLINE CONFIRM; calls
//     DELETE /api/v1/teams/{id}/members/{userId} (→ 204) and refreshes the
//     list; failures surface the SERVER's error message verbatim (including
//     the 409 last-admin protection text) — never a fabricated excuse;
//   • per-member role dropdown calling PATCH .../members/{userId} {role};
//   • both controls are ADMIN-ONLY: hidden entirely from engineer/viewer/
//     guest viewers (server enforces too — the client just doesn't tease).
// Client code is UNVERIFIED against the live server until lane E3a lands.

import { useCallback, useEffect, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import AppShell from "@/components/AppShell";
// M13-G5b (raw-14): visible way back — Command Center → Settings → Team.
import { BreadcrumbTrail, settingsCrumbs } from "@/components/Breadcrumbs";
import { loadSession } from "@/lib/auth";
import {
  TEAM_ROLES,
  TeamInviteDto,
  TeamMemberDto,
  TeamRole,
  createInvite,
  listMembers,
  listPendingInvites,
  redeemInvite,
  removeMember,
  revokeInvite,
  updateMemberRole,
} from "./teamApi";

const cardStyle = { maxWidth: 760, marginBottom: 20 } as const;
const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: 13 } as const;
const thTdStyle = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid var(--border)",
} as const;

const selectStyle = {
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: 6,
  padding: "6px 8px",
} as const;

const rolePillClass: Record<string, string> = {
  admin: "pill green",
  engineer: "pill blue",
  viewer: "pill yellow",
  guest: "pill red",
};

/** Callback bundle for the roster table. Kept explicit so the presentational
 *  component stays pure and SSR-testable without Next routing. */
export interface RosterActions {
  /** First tap on Remove — enters the inline-confirm state for that member. */
  onAskRemove: (userId: string) => void;
  /** Back out of the inline confirm without deleting anyone. */
  onCancelRemove: (userId: string) => void;
  /** Confirmed removal — caller owns the DELETE call + refresh + error text. */
  onConfirmRemove: (userId: string) => void;
  /** Role dropdown commit — caller owns the PATCH call + refresh + errors. */
  onRoleChange: (userId: string, role: TeamRole) => void;
}

/** Presentational members table (H-010 core). Admins get a role <select> and
 *  a Remove flow per row (two-step, inline); non-admin viewers get the same
 *  read-only table Wave 9 shipped — zero management controls rendered. */
export function MembersTable({
  members,
  isAdmin,
  busyUserId,
  pendingRemovalId,
  actions,
}: {
  members: TeamMemberDto[];
  isAdmin: boolean;
  /** Member id whose mutation is in flight (disables that row's controls). */
  busyUserId: string | null;
  /** Member id showing the inline Remove confirmation. */
  pendingRemovalId: string | null;
  actions: RosterActions;
}) {
  return (
    <table style={tableStyle}>
      <thead>
        <tr>
          <th style={thTdStyle}>Name</th>
          <th style={thTdStyle}>Email</th>
          <th style={thTdStyle}>Type</th>
          <th style={thTdStyle}>Role</th>
          {isAdmin && <th style={thTdStyle}>Actions</th>}
        </tr>
      </thead>
      <tbody>
        {members.map((m) => (
          <tr key={m.id}>
            <td style={thTdStyle}>{m.displayName}</td>
            <td style={thTdStyle}>{m.email ?? "—"}</td>
            <td style={thTdStyle}>{m.memberType}</td>
            <td style={thTdStyle}>
              {isAdmin ? (
                <select
                  data-testid={`role-select-${m.id}`}
                  aria-label={`Role for ${m.displayName}`}
                  value={m.role}
                  disabled={busyUserId === m.id}
                  onChange={(e) => actions.onRoleChange(m.id, e.target.value as TeamRole)}
                  style={selectStyle}
                >
                  {/* Unknown server roles still render as the selected option
                      even though they're not in TEAM_ROLES. */}
                  {(TEAM_ROLES.includes(m.role as TeamRole) ? TEAM_ROLES : [m.role as TeamRole, ...TEAM_ROLES]).map(
                    (role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ),
                  )}
                </select>
              ) : (
                <span className={rolePillClass[m.role] ?? "pill blue"}>{m.role}</span>
              )}
            </td>
            {isAdmin && (
              <td style={thTdStyle}>
                {pendingRemovalId === m.id ? (
                  <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                    <span style={{ color: "var(--red)" }}>Remove this member?</span>
                    <button
                      type="button"
                      className="btn danger"
                      data-testid={`confirm-remove-${m.id}`}
                      disabled={busyUserId === m.id}
                      onClick={() => actions.onConfirmRemove(m.id)}
                      aria-label={`Confirm removal of ${m.displayName}`}
                    >
                      {busyUserId === m.id ? "Removing…" : "Yes, remove"}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      data-testid={`cancel-remove-${m.id}`}
                      disabled={busyUserId === m.id}
                      onClick={() => actions.onCancelRemove(m.id)}
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="btn danger"
                    data-testid={`ask-remove-${m.id}`}
                    disabled={busyUserId === m.id}
                    onClick={() => actions.onAskRemove(m.id)}
                    aria-label={`Remove ${m.displayName}`}
                  >
                    Remove
                  </button>
                )}
              </td>
            )}
          </tr>
        ))}
        {members.length === 0 && (
          <tr>
            <td style={thTdStyle} colSpan={isAdmin ? 5 : 4}>
              No members loaded.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

export default function TeamSettingsPage() {
  const [teamId, setTeamId] = useState<string | null>(null);
  const [members, setMembers] = useState<TeamMemberDto[]>([]);
  const [invites, setInvites] = useState<TeamInviteDto[]>([]);

  // invite form
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<TeamRole>("engineer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mintedToken, setMintedToken] = useState<string | null>(null);

  // redeem-by-token form
  const [redeemToken, setRedeemToken] = useState("");
  const [redeemMsg, setRedeemMsg] = useState<string | null>(null);
  const [redeemErr, setRedeemErr] = useState<string | null>(null);

  // roster management (H-010)
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [pendingRemovalId, setPendingRemovalId] = useState<string | null>(null);
  const [rosterError, setRosterError] = useState<string | null>(null);

  // Admin-only controls come straight from the frozen Principal contract
  // (contracts/ts/src/roles.ts): role "admin" sees management controls. Read
  // post-mount so the server prerender and first client render agree (the
  // session lives in localStorage, invisible to SSR).
  const [isAdmin, setIsAdmin] = useState(false);
  useEffect(() => {
    setIsAdmin(loadSession()?.principal?.role === "admin");
  }, []);

  // Members and invites load INDEPENDENTLY: the invites read is admin-only
  // server-side, so a non-admin viewer would otherwise 403 the whole page and
  // never see the roster. Members failing is a real error and still throws.
  const refresh = useCallback(async (team: string) => {
    const token = loadSession()?.token ?? "";
    const [membersRes, invitesRes] = await Promise.allSettled([
      listMembers(team, token),
      listPendingInvites(team, token),
    ]);
    if (membersRes.status === "rejected") throw membersRes.reason;
    setMembers(membersRes.value.items);
    setInvites(invitesRes.status === "fulfilled" ? invitesRes.value.items : []);
  }, []);

  useEffect(() => {
    const session = loadSession();
    if (!session) {
      setError("Not signed in — log in first.");
      return;
    }
    setTeamId(session.principal.teamId);
    refresh(session.principal.teamId).catch((e: unknown) =>
      setError(String((e as Error)?.message ?? e)),
    );
  }, [refresh]);

  const onInviteSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!teamId) return;
    const token = loadSession()?.token ?? "";
    setBusy(true);
    setError(null);
    setNotice(null);
    setMintedToken(null);
    try {
      const minted = await createInvite(teamId, inviteEmail.trim(), inviteRole, token);
      setMintedToken(minted.token); // raw token shown exactly once
      setNotice(`Invite created for ${minted.email} (${minted.role}).`);
      setInviteEmail("");
      await refresh(teamId);
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async (inviteId: string) => {
    if (!teamId) return;
    const token = loadSession()?.token ?? "";
    setBusy(true);
    setError(null);
    try {
      await revokeInvite(teamId, inviteId, token);
      await refresh(teamId);
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const onRedeem = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const session = loadSession();
    if (!session) {
      setRedeemErr("Not signed in — log in first.");
      return;
    }
    setBusy(true);
    setRedeemErr(null);
    setRedeemMsg(null);
    try {
      const out = await redeemInvite(redeemToken.trim(), session.token);
      setRedeemMsg(
        out.alreadyMember
          ? `Already a member of ${out.membership.teamId} (${out.membership.role}).`
          : `Joined team ${out.membership.teamId} as ${out.membership.role}.`,
      );
      setRedeemToken("");
    } catch (e) {
      setRedeemErr(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  // W2-C1 — confirmed member removal. Failures surface the server's message
  // verbatim (e.g. the 409 last-admin text); success clears the confirm state
  // and refetches the roster so the table reflects server truth.
  const onConfirmRemove = async (userId: string) => {
    if (!teamId) return;
    const token = loadSession()?.token ?? "";
    setBusyUserId(userId);
    setRosterError(null);
    try {
      await removeMember(teamId, userId, token);
      setPendingRemovalId(null);
      setError(null);
      setNotice("Member removed from the team.");
      await refresh(teamId);
    } catch (e) {
      setRosterError(String((e as Error)?.message ?? e));
    } finally {
      setBusyUserId(null);
    }
  };

  // W2-C1 — role change via PATCH. The dropdown keeps the SERVER's last known
  // role until a successful refresh, so a failed change visibly snaps back
  // instead of pretending it worked.
  const onRoleChange = async (userId: string, role: TeamRole) => {
    if (!teamId) return;
    const token = loadSession()?.token ?? "";
    setBusyUserId(userId);
    setRosterError(null);
    try {
      await updateMemberRole(teamId, userId, role, token);
      setError(null);
      setNotice(`Role changed to ${role}.`);
      await refresh(teamId);
    } catch (e) {
      setRosterError(String((e as Error)?.message ?? e));
      await refresh(teamId).catch(() => {}); // snap dropdown back to server truth
    } finally {
      setBusyUserId(null);
    }
  };

  const rosterActions = {
    onAskRemove: (userId: string) => {
      setRosterError(null);
      setPendingRemovalId(userId);
    },
    onCancelRemove: (userId: string) => {
      setPendingRemovalId((cur) => (cur === userId ? null : cur));
    },
    onConfirmRemove,
    onRoleChange,
  };

  return (
    <AppShell active="/settings">
      <BreadcrumbTrail items={settingsCrumbs("Team")} />
      <h1>Team</h1>

      {error && (
        <div role="alert" className="pill red" style={{ padding: "8px 12px", marginBottom: 16 }}>
          {error}
        </div>
      )}
      {!error && notice && (
        <div role="status" style={{ color: "var(--green)", marginBottom: 16 }}>
          {notice}
        </div>
      )}

      <section className="card" style={cardStyle}>
        <h2>Members</h2>
        {!isAdmin && (
          <p style={{ color: "var(--muted)", fontSize: 12 }}>
            Roster management (remove / change roles) is restricted to team admins.
          </p>
        )}
        {rosterError && (
          <p role="alert" style={{ color: "var(--red)" }}>
            {rosterError}
          </p>
        )}
        <MembersTable
          members={members}
          isAdmin={isAdmin}
          busyUserId={busyUserId}
          pendingRemovalId={pendingRemovalId}
          actions={rosterActions}
        />
      </section>

      <section className="card" style={cardStyle}>
        <h2>Invite a teammate</h2>
        <form onSubmit={onInviteSubmit} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <label htmlFor="invite-email" style={{ alignSelf: "center" }}>
            Email
          </label>
          <input
            id="invite-email"
            type="email"
            required
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            placeholder="teammate@example.com"
            className="text-input"
            style={{ width: 260 }}
          />
          <label htmlFor="invite-role" style={{ alignSelf: "center" }}>
            Role
          </label>
          <select
            id="invite-role"
            value={inviteRole}
            onChange={(e) => setInviteRole(e.target.value as TeamRole)}
            style={selectStyle}
          >
            {TEAM_ROLES.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
          <button type="submit" className="btn primary" disabled={busy || !teamId}>
            Create invite
          </button>
        </form>

        {mintedToken !== null && (
          <div
            data-testid="minted-token"
            role="status"
            style={{
              marginTop: 12,
              padding: "10px 12px",
              border: "1px solid var(--yellow)",
              borderRadius: 8,
            }}
          >
            <strong style={{ color: "var(--yellow)" }}>Share this link now.</strong> The token is
            stored hashed and will never be shown again:
            <br />
            <code style={{ wordBreak: "break-all" }}>{mintedToken}</code>
          </div>
        )}

        <h2 style={{ marginTop: 18 }}>Pending invites</h2>
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thTdStyle}>Email</th>
              <th style={thTdStyle}>Role</th>
              <th style={thTdStyle}>Expires</th>
              <th style={thTdStyle}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {invites.map((i) => (
              <tr key={i.id}>
                <td style={thTdStyle}>{i.email}</td>
                <td style={thTdStyle}>
                  <span className={rolePillClass[i.role] ?? "pill blue"}>{i.role}</span>
                </td>
                <td style={thTdStyle}>{new Date(i.expiresAt).toLocaleString()}</td>
                <td style={thTdStyle}>
                  <button
                    type="button"
                    className="btn danger"
                    disabled={busy}
                    onClick={() => onRevoke(i.id)}
                    aria-label={`Revoke invite for ${i.email}`}
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
            {invites.length === 0 && (
              <tr>
                <td style={thTdStyle} colSpan={4}>
                  No pending invites.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="card" style={cardStyle}>
        <h2>Join a team with an invite token</h2>
        <form onSubmit={onRedeem} style={{ display: "flex", gap: 8 }}>
          <label htmlFor="redeem-token" style={{ alignSelf: "center" }}>
            Token
          </label>
          <input
            id="redeem-token"
            value={redeemToken}
            onChange={(e) => setRedeemToken(e.target.value)}
            placeholder="paste the invite token"
            className="text-input"
            style={{ flex: 1 }}
            required
          />
          <button type="submit" className="btn" disabled={busy}>
            Redeem
          </button>
        </form>
        {redeemErr && (
          <p role="alert" style={{ color: "var(--red)" }}>
            {redeemErr}
          </p>
        )}
        {redeemMsg && (
          <p role="status" style={{ color: "var(--green)" }}>
            {redeemMsg}
          </p>
        )}
        <p style={{ color: "var(--muted)", fontSize: 12 }}>
          Tokens expire after 7 days and can be used once. Redeeming adds you to that team with
          the invited role.
        </p>
      </section>
    </AppShell>
  );
}

"use client";

// Wave 9 node C — /settings/team: roster with roles, admin invite flow
// (mint → token shown ONCE), pending-invites table with revoke, and
// join-by-token redemption. Real fetches against Section 1, no mocks.
//
// M12-E3b (H-010, pair contract W2-C1) — roster management UI:
//   • per-member Remove button with INLINE CONFIRM; calls
//     DELETE /api/v1/teams/{id}/members/{userId} (→ 204);
//   • per-member role dropdown calling PATCH .../members/{userId} {role};
//   • both controls are ADMIN-ONLY: hidden entirely from engineer/viewer/
//     guest viewers (server enforces too — the client just doesn't tease).
// M14-F4 — both mutations are OPTIMISTIC now (src/hooks/useOptimistic.ts):
// the table reflects the change instantly, the wire response reconciles it
// (role PATCH echoes {userId, role} — commit uses THAT, not the guess), and
// any failure rolls the row back and surfaces the server's own message
// verbatim (e.g. the 409 LAST_ADMIN protection text) — never a fabricated
// excuse. No trailing refetch on success: the mutation response IS the
// server truth for this change, and a refetch would clobber a concurrently
//   in-flight optimistic row.
// Client code is UNVERIFIED against the live server until lane E3a lands.
//
// M14-F2a — invite lifecycle admin UI: the invites panel renders the FULL
// lifecycle (draft/sent/redeemed/expired/revoked) with color+text status
// badges (a11y law: never color alone), Resend on live invites (fresh token
// shown once; the old one is revoked server-side in the same transaction)
// and Revoke on unredeemed ones. Honest empty state; every failure surfaces
// the server's message verbatim. Statuses come from Section 1 — this page
// renders them, it never re-derives them.

import { useCallback, useEffect, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import AppShell from "@/components/AppShell";
import { ErrorState } from "@/components/States";
// M13-G5b (raw-14): visible way back — Command Center → Settings → Team.
import { BreadcrumbTrail, settingsCrumbs } from "@/components/Breadcrumbs";
import { loadSession } from "@/lib/auth";
import { loadErrorMessage } from "@/lib/humanize";
import {
  TEAM_ROLES,
  TeamInviteDto,
  TeamMemberDto,
  TeamRole,
  createInvite,
  listInvites,
  listMembers,
  redeemInvite,
  resendInvite,
  revokeInvite,
} from "./teamApi";
import { changeRoleOptimistic, memberByUserId, removeMemberOptimistic } from "./rosterOps";
// M14-F2a/F4 — presentational tables live in tables.tsx: Next 15's generated
// route-type check rejects any non-default export from a page module, so the
// wave-9 "export MembersTable from the page" pattern moved out of page.tsx
// (ATLAS gate). Same components, same props — only the module boundary moved.
// M14-F4 note: MembersTable/RosterActions stay defined in THIS file (F4 owns
// them here); only InvitesTable/InviteActions live in tables.tsx (F2a).
// M14-F2b unblock: until F4 re-lands its in-page copy, MembersTable must come
// from ./tables (its current home) or <MembersTable> below is undefined.
import {
  InvitesTable,
  MembersTable,
  type InviteActions,
  type RosterActions,
} from "./tables";

const cardStyle = { maxWidth: 760, marginBottom: 20 } as const;

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

const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: 13 } as const;
const thTdStyle = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid var(--border)",
} as const;

// M14-F2a — badge classes for invite statuses (kept here only if referenced).

/**
 * M14-F4 — pure rollback merge for optimistic roster mutations. Restores the
 * pre-mutation snapshot rows WITHOUT resurrecting rows the user deleted in
 * the same session, and without clobbering rows mutated since (last-write
 * wins for concurrent changes). Order follows the CURRENT list; snapshot-only
 * survivors rejoin at their original depth.
 */

/** Callback bundle for the roster table. Kept explicit so the presentational
 *  component stays pure and SSR-testable without Next routing. */

// M14-F4 — roster rollback is ROW-SCOPED via restoreRow (./tables.tsx): each
// mutation restores only the ONE row it touched, so a failed removal can never
// zombie-resurrect rows other confirmed actions deleted, and a failed role
// change never clobbers changes made since to other rows.

// M14-DS migration completion (F2b cross-lane unblock): the roster table and
// its RosterActions contract moved to ./tables.tsx; the stale copy that
// remained here referenced deleted consts and broke tsc for every lane.
// MembersTable is imported from ./tables at the top of this file.

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
  const [redeemEmail, setRedeemEmail] = useState("");
  const [redeemMsg, setRedeemMsg] = useState<string | null>(null);
  const [redeemErr, setRedeemErr] = useState<string | null>(null);

  // roster management (H-010)
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [pendingRemovalId, setPendingRemovalId] = useState<string | null>(null);
  const [rosterError, setRosterError] = useState<string | null>(null);

  // M14-F2a — invite lifecycle mutations
  const [busyInviteId, setBusyInviteId] = useState<string | null>(null);
  // Operability slice 4: Revoke is destructive, so it asks in the row first.
  const [pendingRevokeId, setPendingRevokeId] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);

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
  // M14-F2a: the invites read is the FULL lifecycle list now (every status).
  const refresh = useCallback(async (team: string) => {
    const token = loadSession()?.token ?? "";
    const [membersRes, invitesRes] = await Promise.allSettled([
      listMembers(team, token),
      listInvites(team, token),
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
      setError(loadErrorMessage(e)),
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

  // M14-F2a — lifecycle actions on the InvitesTable. Both surface the SERVER's
  // message verbatim on failure and refetch so badges always show server truth.
  // Resend shows the fresh token exactly once, same law as mint: it is stored
  // hashed server-side and never leaves the response again.
  const onInviteResend = async (inviteId: string) => {
    if (!teamId) return;
    const token = loadSession()?.token ?? "";
    setBusyInviteId(inviteId);
    setInviteError(null);
    setNotice(null);
    try {
      const resent = await resendInvite(teamId, inviteId, token);
      setMintedToken(resent.token); // one-time display, same as a fresh mint
      setNotice(`Fresh invite link created for ${resent.email}. The old link no longer works.`);
      await refresh(teamId);
    } catch (e) {
      setInviteError(String((e as Error)?.message ?? e));
    } finally {
      setBusyInviteId(null);
    }
  };

  const onInviteRevoke = async (inviteId: string) => {
    if (!teamId) return;
    const token = loadSession()?.token ?? "";
    setBusyInviteId(inviteId);
    setInviteError(null);
    setPendingRevokeId(null);
    try {
      await revokeInvite(teamId, inviteId, token);
      await refresh(teamId); // row stays visible with status "revoked"
    } catch (e) {
      setInviteError(String((e as Error)?.message ?? e));
    } finally {
      setBusyInviteId(null);
    }
  };

  const inviteActions: InviteActions = {
    onResend: (id: string) => void onInviteResend(id),
    onRevoke: (id: string) => void onInviteRevoke(id),
    onAskRevoke: (id: string) => {
      setInviteError(null);
      setPendingRevokeId(id);
    },
    onCancelRevoke: (id: string) => setPendingRevokeId((cur) => (cur === id ? null : cur)),
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
      const out = await redeemInvite(redeemToken.trim(), redeemEmail.trim(), session.token);
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

  // M14-F4 — confirmed member removal, now OPTIMISTIC (W2-C1 semantics kept):
  // the row vanishes the moment Remove is confirmed, the DELETE reconciles it,
  // and any failure restores the exact prior row + surfaces the server's own
  // message verbatim (e.g. the 409 LAST_ADMIN protection text). No trailing
  // refetch on success — the mutation response IS server truth for this
  // change, and a refetch would clobber a concurrently in-flight optimistic
  // row. The confirm state clears immediately either way.
  // Operability slice 4: the mutations live in ./rosterOps (same optimistic apply / row-scoped
  // rollback as M14-F4) so the button-to-URL path is tested. Actions carry the user id (D3).
  const store = { get: () => members, set: setMembers };

  const onConfirmRemove = async (userId: string) => {
    if (!teamId) return;
    const row = memberByUserId(members, userId);
    if (!row) return;
    setBusyUserId(userId);
    setRosterError(null);
    setPendingRemovalId(null);
    const out = await removeMemberOptimistic(store, teamId, row, loadSession()?.token ?? "");
    if (out.ok) {
      setError(null);
      setNotice("Member removed from the team.");
    } else {
      setRosterError(out.error);
    }
    setBusyUserId(null);
  };

  const onRoleChange = async (userId: string, role: TeamRole) => {
    if (!teamId) return;
    const row = memberByUserId(members, userId);
    if (!row) return;
    setBusyUserId(userId);
    setRosterError(null);
    const out = await changeRoleOptimistic(store, teamId, row, role, loadSession()?.token ?? "");
    if (!out.ok) setRosterError(out.error);
    setBusyUserId(null);
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

      {error &&
        (members.length === 0 ? (
          <ErrorState
            title="The team roster didn’t load"
            message={error}
            onRetry={() => {
              if (!teamId) return;
              setError(null);
              refresh(teamId).catch((e: unknown) => setError(loadErrorMessage(e)));
            }}
          />
        ) : (
          <div role="alert" className="pill red" style={{ padding: "8px 12px", marginBottom: 16 }}>
            {error}
          </div>
        ))}
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

        <h2 style={{ marginTop: 18 }}>Invites</h2>
        {inviteError && (
          <p role="alert" style={{ color: "var(--red)" }}>
            {inviteError}
          </p>
        )}
        {!isAdmin && (
          <p style={{ color: "var(--muted)", fontSize: 12 }}>
            Invite management (resend / revoke) is restricted to team admins.
          </p>
        )}
        {/* M14-F2a — full lifecycle table with color+text status badges. */}
        <InvitesTable
          invites={invites}
          busyInviteId={busyInviteId}
          pendingRevokeId={pendingRevokeId}
          actions={inviteActions}
        />
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
          <label htmlFor="redeem-email" style={{ alignSelf: "center" }}>
            Invited email
          </label>
          <input
            id="redeem-email"
            type="email"
            value={redeemEmail}
            onChange={(e) => setRedeemEmail(e.target.value)}
            placeholder="the address the invite was sent to"
            className="text-input"
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

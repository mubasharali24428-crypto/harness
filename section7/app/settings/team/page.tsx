"use client";

// Wave 9 node C — /settings/team: roster with roles, admin invite flow
// (mint → token shown ONCE), pending-invites table with revoke, and
// join-by-token redemption. Real fetches against Section 1, no mocks.

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
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
  revokeInvite,
} from "./teamApi";

const cardStyle = { maxWidth: 760, marginBottom: 20 } as const;
const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: 13 } as const;
const thTdStyle = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid var(--border)",
} as const;

const rolePillClass: Record<string, string> = {
  admin: "pill green",
  engineer: "pill blue",
  viewer: "pill yellow",
  guest: "pill red",
};

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

  const refresh = useCallback(async (team: string) => {
    const token = loadSession()?.token ?? "";
    const [membersRes, invitesRes] = await Promise.all([
      listMembers(team, token),
      listPendingInvites(team, token),
    ]);
    setMembers(membersRes.items);
    setInvites(invitesRes.items);
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

  return (
    <AppShell active="/settings">
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
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thTdStyle}>Name</th>
              <th style={thTdStyle}>Email</th>
              <th style={thTdStyle}>Type</th>
              <th style={thTdStyle}>Role</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td style={thTdStyle}>{m.displayName}</td>
                <td style={thTdStyle}>{m.email ?? "—"}</td>
                <td style={thTdStyle}>{m.memberType}</td>
                <td style={thTdStyle}>
                  <span className={rolePillClass[m.role] ?? "pill blue"}>{m.role}</span>
                </td>
              </tr>
            ))}
            {members.length === 0 && (
              <tr>
                <td style={thTdStyle} colSpan={4}>
                  No members loaded.
                </td>
              </tr>
            )}
          </tbody>
        </table>
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
            style={{
              background: "var(--panel-2)",
              border: "1px solid var(--border)",
              color: "var(--text)",
              borderRadius: 6,
              padding: "6px 8px",
            }}
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

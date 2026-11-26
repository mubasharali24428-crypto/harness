"use client";

// M14-F2a + M14-F4 — presentational tables for the /settings/team page.
// Split out of page.tsx (ATLAS gate): Next 15's generated route-type check
// rejects ANY value export from a page module, so the wave-9 pattern of
// exporting MembersTable from the page file no longer typechecks. The page
// imports these; vitest imports these — page.tsx keeps only its default export.
//
// M14-DS migration: shared primitives replace the page-local style maps.
//   • role/status badges → <StatusPill> (tokens.statusTones semantics;
//     invite lifecycle tones registered alongside the roster vocabulary);
//   • action buttons → <Button> (variant faces identical to .btn/.btn.danger);
//   • table shells → <Table> (keeps F2b's .table-scroll overflow wrapper).
// Markup contract unchanged: every data-testid, aria-label, disabled rule,
// inline-confirm flow and empty-state string is byte-for-byte the same.

import * as React from "react";
import { TEAM_ROLES, TeamInviteDto, TeamMemberDto, TeamRole } from "./teamApi";
import { Button, StatusPill, Table, cellStyle } from "@/components/ui";
import { type StatusTone } from "@/components/tokens";

const selectStyle = {
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: 6,
  padding: "6px 8px",
} as const;

// M14-DS — badge tones live in tokens.statusTones (single semantic map for
// roster roles AND invite statuses). Kept here only as documentation of the
// mapping; unknown server values still degrade to the neutral blue pill via
// toneForStatus, satisfying the F2a a11y law (never color alone — every badge
// renders its raw text label).
const INVITE_STATUS_DOC: Record<string, StatusTone> = {
  draft: "yellow", // not yet sent — waiting on the sender
  sent: "blue", // live, waiting for redemption
  redeemed: "green", // joined — terminal success
  expired: "yellow", // TTL passed — terminal failure
  revoked: "red", // killed by an admin or superseded by resend
};
void INVITE_STATUS_DOC;

/** M14-F2a — callback bundle for the lifecycle table. Presentational component
 *  stays pure and SSR-testable without Next routing (same pattern as roster). */
export interface InviteActions {
  /** Resend pressed — caller owns the POST + refresh + one-time token UI. */
  onResend: (inviteId: string) => void;
  /** Revoke confirmed — caller owns the DELETE + refresh + error text. */
  onRevoke: (inviteId: string) => void;
  /** Operability slice 4: first press on Revoke asks in the row. Without it, Revoke acts at once
   *  (the pre-slice behaviour, kept for callers that confirm elsewhere). */
  onAskRevoke?: (inviteId: string) => void;
  /** Back out of the in-row revoke confirm. */
  onCancelRevoke?: (inviteId: string) => void;
}

/** Presentational invites lifecycle table (M14-F2a core). One row per invite
 *  in ANY status, each with its color+text badge and the actions that status
 *  allows: draft/sent can be resent or revoked; terminal statuses show "—". */
export function InvitesTable({
  invites,
  busyInviteId,
  pendingRevokeId = null,
  actions,
}: {
  invites: TeamInviteDto[];
  /** Invite id whose mutation is in flight (disables that row's buttons). */
  busyInviteId: string | null;
  /** Invite id showing the in-row "Revoke this invite?" confirm. */
  pendingRevokeId?: string | null;
  actions: InviteActions;
}) {
  return (
    <Table caption="Team invites with lifecycle status">
      <thead>
        <tr>
          <th style={cellStyle}>Email</th>
          <th style={cellStyle}>Role</th>
          <th style={cellStyle}>Status</th>
          <th style={cellStyle}>Expires</th>
          <th style={cellStyle}>Actions</th>
        </tr>
      </thead>
      <tbody>
        {invites.map((i) => (
          <tr key={i.id} data-testid={`invite-row-${i.id}`}>
            <td style={cellStyle}>{i.email}</td>
            <td style={cellStyle}>
              <StatusPill status={i.role}>{i.role}</StatusPill>
            </td>
            <td style={cellStyle}>
              <StatusPill status={i.status}>{i.status}</StatusPill>
            </td>
            <td style={cellStyle}>{new Date(i.expiresAt).toLocaleString()}</td>
            <td style={cellStyle}>
              {(i.status === "draft" || i.status === "sent") && pendingRevokeId === i.id ? (
                <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                  <span style={{ color: "var(--red)" }}>Revoke this invite? The link stops working.</span>
                  <Button
                    variant="danger"
                    testId={`confirm-revoke-${i.id}`}
                    loading={busyInviteId === i.id}
                    loadingText="Revoking…"
                    onClick={() => actions.onRevoke(i.id)}
                    aria-label={`Confirm revoking the invite for ${i.email}`}
                  >
                    Yes, revoke
                  </Button>
                  <Button
                    variant="secondary"
                    testId={`cancel-revoke-${i.id}`}
                    disabled={busyInviteId === i.id}
                    onClick={() => actions.onCancelRevoke?.(i.id)}
                    aria-label={`Keep the invite for ${i.email}`}
                  >
                    Cancel
                  </Button>
                </span>
              ) : i.status === "draft" || i.status === "sent" ? (
                <>
                  <Button
                    variant="secondary"
                    testId={`resend-invite-${i.id}`}
                    disabled={busyInviteId === i.id}
                    onClick={() => actions.onResend(i.id)}
                    aria-label={`Resend invite for ${i.email}`}
                  >
                    {busyInviteId === i.id ? "Resending…" : "Resend"}
                  </Button>{" "}
                  <Button
                    variant="danger"
                    testId={`revoke-invite-${i.id}`}
                    disabled={busyInviteId === i.id}
                    onClick={() => (actions.onAskRevoke ? actions.onAskRevoke(i.id) : actions.onRevoke(i.id))}
                    aria-label={`Revoke invite for ${i.email}`}
                  >
                    Revoke
                  </Button>
                </>
              ) : (
                <span style={{ color: "var(--muted)" }}>—</span>
              )}
            </td>
          </tr>
        ))}
        {invites.length === 0 && (
          <tr>
            <td style={cellStyle} colSpan={5}>
              No invites yet — create one above.
            </td>
          </tr>
        )}
      </tbody>
    </Table>
  );
}

/**
 * M14-F4 — ROW-SCOPED optimistic rollback. Restores exactly the ONE member
 * this mutation touched, from its pre-mutation snapshot: re-appended if it
 * was optimistically removed, or put back in place if a role change is being
 * undone. Never resurrects rows OTHER actions removed and never clobbers
 * changes made since to other rows — each mutation owns only its own row's
 * rollback (list-level snapshot merges would zombie-resurrect deleted rows).
 */
export function restoreRow(
  current: TeamMemberDto[],
  snapshotRow: TeamMemberDto,
): TeamMemberDto[] {
  const idx = current.findIndex((m) => m.id === snapshotRow.id);
  if (idx === -1) return [...current, snapshotRow]; // undo an optimistic removal
  return current.map((m) => (m.id === snapshotRow.id ? snapshotRow : m));
}

/**
 * M14-F4 (moved here from page.tsx by the DS module split, completed by F2b
 * after the move stranded F4's imports) — legacy list-level rollback entry.
 * Delegates to restoreRow for every snapshot row (each mutation owns only its
 * own row's rollback).
 */
export function mergeRestored(
  current: TeamMemberDto[],
  snapshot: TeamMemberDto[],
): TeamMemberDto[] {
  return snapshot.reduce((acc, row) => restoreRow(acc, row), [...current]);
}

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
  rowError = null,
  actions,
}: {
  members: TeamMemberDto[];
  isAdmin: boolean;
  /** Member id whose mutation is in flight (disables that row's controls). */
  busyUserId: string | null;
  /** Member id showing the inline Remove confirmation. */
  pendingRemovalId: string | null;
  /** F5 (live check 2026-11-26): a failed remove / role change, shown in that member's row. */
  rowError?: { userId: string; message: string } | null;
  actions: RosterActions;
}) {
  return (
    <Table caption="Team members and roles">
      <thead>
        <tr>
          <th style={cellStyle}>Name</th>
          <th style={cellStyle}>Email</th>
          <th style={cellStyle}>Type</th>
          <th style={cellStyle}>Role</th>
          {isAdmin && <th style={cellStyle}>Actions</th>}
        </tr>
      </thead>
      <tbody>
        {members.map((m) => (
          <tr key={m.id}>
            <td style={cellStyle}>{m.displayName}</td>
            <td style={cellStyle}>{m.email ?? "—"}</td>
            <td style={cellStyle}>{m.memberType}</td>
            <td style={cellStyle}>
              {isAdmin && m.userId ? (
                <select
                  data-testid={`role-select-${m.userId}`}
                  aria-label={`Role for ${m.displayName}`}
                  value={m.role}
                  disabled={busyUserId === m.userId}
                  onChange={(e) => actions.onRoleChange(m.userId as string, e.target.value as TeamRole)}
                  style={selectStyle}
                >
                  {/* Unknown server roles still render as the selected option
                      even though they're not in TEAM_ROLES. */}
                  {(TEAM_ROLES.includes(m.role as TeamRole)
                    ? TEAM_ROLES
                    : [m.role as TeamRole, ...TEAM_ROLES]
                  ).map((role) => (
                    <option key={role} value={role}>
                      {role}
                    </option>
                  ))}
                </select>
              ) : (
                <StatusPill status={m.role}>{m.role}</StatusPill>
              )}
            </td>
            {isAdmin && (
              <td style={cellStyle}>
                {!m.userId ? (
                  "—" /* agent member: no user id, no roster mutation route */
                ) : pendingRemovalId === m.userId ? (
                  <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                    <span style={{ color: "var(--red)" }}>Remove this member?</span>
                    <Button
                      variant="danger"
                      testId={`confirm-remove-${m.userId}`}
                      loading={busyUserId === m.userId}
                      loadingText="Removing…"
                      onClick={() => actions.onConfirmRemove(m.userId as string)}
                      aria-label={`Confirm removal of ${m.displayName}`}
                    >
                      Yes, remove
                    </Button>
                    <Button
                      variant="secondary"
                      testId={`cancel-remove-${m.userId}`}
                      disabled={busyUserId === m.userId}
                      onClick={() => actions.onCancelRemove(m.userId as string)}
                    >
                      Cancel
                    </Button>
                  </span>
                ) : (
                  <Button
                    variant="danger"
                    testId={`ask-remove-${m.userId}`}
                    disabled={busyUserId === m.userId}
                    onClick={() => actions.onAskRemove(m.userId as string)}
                    aria-label={`Remove ${m.displayName}`}
                  >
                    Remove
                  </Button>
                )}
                {m.userId && rowError?.userId === m.userId && (
                  <p role="alert" id={`row-error-${m.userId}`} tabIndex={-1} data-testid={`row-error-${m.userId}`}
                    style={{ color: "var(--red)", margin: "6px 0 0" }}>
                    {rowError.message}
                  </p>
                )}
              </td>
            )}
          </tr>
        ))}
        {members.length === 0 && (
          <tr>
            <td style={cellStyle} colSpan={isAdmin ? 5 : 4}>
              No members loaded.
            </td>
          </tr>
        )}
      </tbody>
    </Table>
  );
}

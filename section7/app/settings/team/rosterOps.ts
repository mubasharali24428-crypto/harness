// Operability slice 4: the roster mutations as framework-free functions, so the whole path from a
// table button to the wire URL is testable in node-env vitest. Same behaviour the page had
// (M14-F4 optimistic apply, row-scoped rollback, server message on failure); the page now calls
// these instead of inlining them.
//
// D3: the member routes key on the USER id. A membership row's `id` is a React key only and is
// never sent: every call here takes the row and reads `row.userId`, and refuses a row without one.
import { runOptimistic } from "@/hooks/useOptimistic";
import { removeMember, updateMemberRole, type TeamMemberDto, type TeamRole } from "./teamApi";
import { restoreRow } from "./tables";

export interface MembersStore {
  get: () => TeamMemberDto[];
  set: (next: TeamMemberDto[] | ((cur: TeamMemberDto[]) => TeamMemberDto[])) => void;
}

export type RosterResult = { ok: true } | { ok: false; error: string };

const NO_USER = "This member is an agent and has no user account; it cannot be removed or re-roled here.";

/** Find the row an action names. Actions carry the user id (D3), never the membership id. */
export function memberByUserId(members: TeamMemberDto[], userId: string): TeamMemberDto | null {
  return members.find((m) => m.userId === userId) ?? null;
}

/** Put a removed row back at its old position (clamped); a row still present is replaced in place. */
export function restoreAt(current: TeamMemberDto[], row: TeamMemberDto, index: number): TeamMemberDto[] {
  if (current.some((m) => m.id === row.id)) return restoreRow(current, row);
  const at = index < 0 ? current.length : Math.min(index, current.length);
  return [...current.slice(0, at), row, ...current.slice(at)];
}

/** Confirmed removal: the row leaves the list at once; a failed DELETE puts exactly that row back. */
export async function removeMemberOptimistic(
  store: MembersStore, teamId: string, row: TeamMemberDto, token: string,
): Promise<RosterResult> {
  const userId = row.userId;
  if (!userId) return { ok: false, error: NO_USER };
  let error: string | null = null;
  const outcome = await runOptimistic<TeamMemberDto[], null>({
    apply: () => {
      const snapshot = store.get();
      store.set((cur) => cur.filter((m) => m.userId !== userId));
      return snapshot;
    },
    request: () => removeMember(teamId, userId, token),
    // The row comes back where it was, so a failed removal looks like nothing moved.
    rollback: (snapshot) => store.set((cur) => restoreAt(cur, row, snapshot.findIndex((m) => m.id === row.id))),
    onError: (message) => {
      error = message;
    },
  });
  return outcome === "confirmed" ? { ok: true } : { ok: false, error: error ?? "The member was not removed." };
}

/** Role change: shown at once, reconciled with the server's echoed role, rolled back on failure. */
export async function changeRoleOptimistic(
  store: MembersStore, teamId: string, row: TeamMemberDto, role: TeamRole, token: string,
): Promise<RosterResult> {
  const userId = row.userId;
  if (!userId) return { ok: false, error: NO_USER };
  let error: string | null = null;
  const outcome = await runOptimistic<TeamMemberDto[], { userId: string; role: string }>({
    apply: () => {
      const snapshot = store.get();
      store.set((cur) => cur.map((m) => (m.userId === userId ? { ...m, role } : m)));
      return snapshot;
    },
    request: () => updateMemberRole(teamId, userId, role, token),
    commit: (res) => store.set((cur) => cur.map((m) => (m.userId === res.userId ? { ...m, role: res.role } : m))),
    rollback: () => store.set((cur) => restoreRow(cur, row)),
    onError: (message) => {
      error = message;
    },
  });
  return outcome === "confirmed" ? { ok: true } : { ok: false, error: error ?? "The role was not changed." };
}

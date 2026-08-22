// Mirrors contracts/src/harness_contracts/roles.py.

export const Role = {
  ADMIN: "admin",
  ENGINEER: "engineer",
  VIEWER: "viewer",
  GUEST: "guest",
} as const;

export type Role = (typeof Role)[keyof typeof Role];

export interface Principal {
  userId: string;
  teamId: string;
  role: Role;
}

export const CAN_APPROVE_PLANS: ReadonlySet<Role> = new Set([Role.ADMIN, Role.ENGINEER]);
export const READ_ONLY: ReadonlySet<Role> = new Set([Role.VIEWER, Role.GUEST]);

export type TeamScopedAction = "read" | "write" | "approve" | "merge_gate";

export function can(principal: Principal, action: TeamScopedAction): boolean {
  if (action === "read") return true;
  if (READ_ONLY.has(principal.role)) return false;
  if (action === "approve") return CAN_APPROVE_PLANS.has(principal.role);
  return true;
}

// Multiplayer pilot S3 — client for Section 4's shared-run collaboration
// routes (see section4/src/harness_orchestrator/collab.py for the rules).
// Identity is the bearer token only; the server never trusts an actor in the
// body. Base URL / error handling mirror planGateApi.ts.
import { ApiError, parseApiError } from "@/lib/api";
import { PLAN_GATE_BASE } from "../../../../dashboard/planGateApi";

export type ParticipantRole = "leader" | "participant" | "observer";
export type RedirectKind = "guidance" | "scope_change" | "permission_change";
export type ApprovalState =
  | "pending" | "approved" | "rejected" | "invalidated"
  | "expired" | "executing" | "completed" | "failed";

export interface Roster {
  participants: { userId: string; role: ParticipantRole }[];
  leader: string | null;
  pendingHandoff: { id: string; from: string; to: string } | null;
}
export interface Approval {
  id: string;
  runId: string;
  action: "delete" | "modify";
  paths: string[];
  summary: string;
  diff?: string;
  digest: string;
  state: ApprovalState;
  requester: string;
  decidedBy: string | null;
  outcome: string | null;
  expiresAt: string;
}
export interface CollabEvent {
  seq: number;
  actor: string;
  kind: string;
  payload: Record<string, unknown>;
  ts: string;
}

const root = (runId: string) =>
  `${PLAN_GATE_BASE}/api/v1/orchestrator/runs/${encodeURIComponent(runId)}`;

async function call<T>(url: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    const { message, code } = parseApiError(res.status, body);
    throw new ApiError(message, code, res.status);
  }
  return body as T;
}
const post = <T,>(url: string, token: string, body: unknown = {}) =>
  call<T>(url, token, { method: "POST", body: JSON.stringify(body) });

export const getRoster = (runId: string, t: string) =>
  call<Roster>(`${root(runId)}/participants`, t);
export const joinRun = (runId: string, t: string) => post<Roster>(`${root(runId)}/join`, t);
export const claimLeader = (runId: string, t: string) => post<Roster>(`${root(runId)}/leader`, t);
export const sendRedirect = (
  runId: string,
  t: string,
  b: { kind: RedirectKind; content: string; reason?: string; requestId?: string },
) => post<{ seq: number; duplicate: boolean; invalidatedApprovals: number }>(`${root(runId)}/redirect`, t, b);
export const getHistory = (runId: string, t: string, after = 0) =>
  call<{ events: CollabEvent[] }>(`${root(runId)}/collab-history?after=${after}`, t);
export const requestHandoff = (runId: string, t: string, toUserId: string) =>
  post<{ handoffId: string }>(`${root(runId)}/handoff`, t, { toUserId });
export const decideHandoff = (runId: string, t: string, handoffId: string, accept: boolean) =>
  post<unknown>(`${root(runId)}/handoff/${encodeURIComponent(handoffId)}`, t, { accept });
export const listApprovals = (runId: string, t: string) =>
  call<{ approvals: Approval[] }>(`${root(runId)}/approvals`, t);
/** The approver echoes the digest they reviewed; a changed proposal is refused (409). */
export const decideApproval = (runId: string, t: string, a: Approval, approve: boolean) =>
  post<Approval>(`${root(runId)}/approvals/${encodeURIComponent(a.id)}/decision`, t, {
    approve,
    digest: a.digest,
  });

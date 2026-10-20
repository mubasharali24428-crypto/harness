// Multiplayer pilot S3 — client for Section 4's shared-run collaboration
// routes (see section4/src/harness_orchestrator/collab.py for the rules).
// Identity is the bearer token only; the server never trusts an actor in the
// body. Base URL / error handling mirror planGateApi.ts.
import { ApiError, IDENTITY_BASE, parseApiError } from "@/lib/api";
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
  decidedAt: string | null; // ISO-8601 UTC; null until decided, and again once an amend voids the decision
  amendedAfterApproval?: boolean; // the approvals list only: pending because an amend voided an approval
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

export const root = (runId: string) =>
  `${PLAN_GATE_BASE}/api/v1/orchestrator/runs/${encodeURIComponent(runId)}`;

export async function call<T>(url: string, token: string, init?: RequestInit): Promise<T> {
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
    const err: ApiError & { retryAfter?: number } = new ApiError(message, code, res.status);
    // 429: keep Retry-After (seconds) so the UI can say how long a control stays disabled (docs/realtime.md 5.3/5.4).
    const ra = Number(res.headers?.get?.("retry-after"));
    if (Number.isFinite(ra) && ra > 0) err.retryAfter = ra;
    throw err;
  }
  return body as T;
}
export const post = <T,>(url: string, token: string, body: unknown = {}) =>
  call<T>(url, token, { method: "POST", body: JSON.stringify(body) });

export const getRoster = (runId: string, t: string) =>
  call<Roster>(`${root(runId)}/participants`, t);
export const joinRun = (runId: string, t: string) => post<Roster>(`${root(runId)}/join`, t);
export const claimLeader = (runId: string, t: string) => post<Roster>(`${root(runId)}/leader`, t);
export const sendRedirect = (
  runId: string,
  t: string,
  b: { kind: RedirectKind; content: string; reason?: string; requestId?: string },
) => post<{ seq: number | null; duplicate: boolean; invalidatedApprovals: number; queued?: boolean; instructionId?: string }>(`${root(runId)}/redirect`, t, b);
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

/** Apply exactly the approved staged digest; approval alone does not land files. */
export const applyApproval = (runId: string, t: string, a: Approval) =>
  post<Approval>(`${root(runId)}/approvals/${encodeURIComponent(a.id)}/apply`, t, { digest: a.digest });
export const hasUnappliedStaged = (approvals: Approval[]) => approvals.some((a) =>
  a.requester === "system:staging" && ["pending", "approved", "executing"].includes(a.state));

// Server push (part 11): a one-time ticket, then a socket whose frames are only a
// wake-up - the caller re-reads over HTTP, so a lost or duplicated frame is harmless.
// Returns a closer. `onState(false)` means "poll faster": the socket is down.
export function openCollabSocket(
  projectId: string, runId: string, token: string, lastSeq: () => number,
  onWake: () => void, onState: (live: boolean) => void,
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const connect = async () => {
    try {
      const t = await post<{ ticket: string; fingerprint?: string }>(
        `${IDENTITY_BASE}/api/v1/ws-ticket`, token, { projectId });
      if (closed) return;
      const base = IDENTITY_BASE.replace(/^http/, "ws");
      const q = new URLSearchParams({ ticket: t.ticket, afterSeq: String(lastSeq()) });
      if (t.fingerprint) q.set("fingerprint", t.fingerprint);
      ws = new WebSocket(
        `${base}/api/v1/ws/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/collab?${q}`);
      ws.onopen = () => onState(true);
      ws.onmessage = () => onWake();
      ws.onclose = () => {
        onState(false);
        if (!closed) retry = setTimeout(() => void connect(), 5000);
      };
    } catch {
      onState(false);
      if (!closed) retry = setTimeout(() => void connect(), 5000);
    }
  };
  void connect();
  return () => { closed = true; clearTimeout(retry); ws?.close(); };
}

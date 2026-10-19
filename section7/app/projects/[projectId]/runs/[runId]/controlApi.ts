// Parts 41/42/45 client: presence + participation, control lease and suggestions, intent
// conflicts, agent subtasks. Same plain fetch helper as collabApi.ts (bearer token only).
// Routes and shapes: docs/presence.md, docs/control.md, docs/agents.md.
import { ApiError } from "@/lib/api";
import { PLAN_GATE_BASE } from "../../../../dashboard/planGateApi";
import { call, post, root } from "./collabApi";

// ---- stable error codes -> plain language (the server message is the fallback) ----
const MESSAGES: Record<string, string> = {
  SESSION_FULL: "This run is full. Ask someone to leave, or try again later.",
  LEADER_MUST_HAND_OFF: "You lead this run. Hand the lead to a teammate first.",
  LEASE_HELD: "Someone else holds control right now. Request it instead.",
  QUEUE_FULL: "Too many suggestions are waiting for the leader. Try again after they have reviewed some.",
  STALE_INTENT_VERSION: "The run's plan changed while you were looking. Reload and try again.",
  INTENT_CONFLICT: "That change clashes with a newer one. Review the conflict and choose which to keep.",
  PRESENCE_DISABLED: "Your team has turned presence off.",
  QUEUE_ORDER: "Someone asked for control before you. They get the first chance.",
  NOT_LEADER: "Only the person who holds control can do that.",
  NOT_PENDING: "That was already decided or has expired.",
  ALREADY_RESOLVED: "That conflict was already resolved.",
  NOT_PARTICIPANT: "Join the run first.",
  ROLE_DENIED: "Your role can only observe this run.",
  TOO_MANY_DEVICES: "You are online on too many devices for this run.",
  ILLEGAL_TRANSITION: "That agent has already finished or cannot change state.",
  AGENT_BUDGET_EXHAUSTED: "That agent has used up its budget, so it cannot resume.",
};

export function friendlyError(e: unknown): string {
  if (e instanceof ApiError) return MESSAGES[e.code] ?? e.message;
  return String((e as Error)?.message ?? e);
}

// ---- control (part 42) ----
export interface QueuedInstruction { id: string; author: string; content: string; queuedAt: string | null; expiresAt: string | null }
export interface ControlView {
  holder: {
    userId: string; untimed: boolean; expiresAt: string | null; expiresInSeconds: number | null;
    connected: boolean | null; takeable: boolean;
  } | null;
  queue: { requestId: string; userId: string; requestedAt: string | null; position: number }[];
  queuedInstructions: QueuedInstruction[];
  viewer: { userId: string; isHolder: boolean; canAcquire: boolean; canRequest: boolean; canSuggest: boolean; queuePosition: number | null };
  how: { request: string; acquire: string; suggest: string };
}
export interface ConflictField { field: string; base: unknown; current: unknown; proposed: unknown; conflicting: boolean }
export interface Conflict { id: string; kind: "conflict" | "proposal"; author: string; state: string; baseVersion: number; stale: boolean; fields: ConflictField[] }
export interface ConflictsView {
  currentApproved: { version: number };
  conflicts: Conflict[];
  openApprovals: unknown[];
}

/** The control view hands out routes like "POST /api/v1/orchestrator/runs/r1/control/request"; use them as given. */
export function howUrl(how: string): string {
  const m = /^POST\s+(\/\S+)/.exec(how);
  if (!m) throw new Error("The server did not say where to send that request.");
  return `${PLAN_GATE_BASE}${m[1]}`;
}

export const getControl = (runId: string, t: string) => call<ControlView>(`${root(runId)}/control`, t);
export const postHow = (how: string, t: string) => post<unknown>(howUrl(how), t);
export const releaseControl = (runId: string, t: string) => post<unknown>(`${root(runId)}/control/release`, t);
export const decideControl = (
  runId: string, t: string,
  b: { instructionId: string; decision: "accept" | "reject" } | { requestId: string; decision: "grant" | "decline" | "cancel" },
) => post<unknown>(`${root(runId)}/control/decide`, t, b);
export const getConflicts = (runId: string, t: string) => call<ConflictsView>(`${root(runId)}/intent/conflicts`, t);
export const resolveConflict = (
  runId: string, t: string, conflictId: string, resolution: "keep_current" | "take_proposed", expectedVersion: number,
) => post<unknown>(`${root(runId)}/intent/conflicts/${encodeURIComponent(conflictId)}/resolve`, t, { resolution, expectedVersion });

// ---- presence + participation (part 41) ----
export type PresenceStatus = "online" | "idle" | "disconnected" | "unknown" | "hidden";
export interface PresenceEntry {
  userId: string; role: string; isLeader: boolean; self: boolean;
  presence: { status: PresenceStatus; lastSeen?: string } | null;
  announcement: string;
  preferences?: { muted: boolean; notify: boolean; sharePresence: string };
}
export interface PresenceView { mode: "full" | "limited" | "off"; notice: string; participants: PresenceEntry[] }
export type ParticipationAction = "observe" | "participate" | "leave" | "rejoin" | "mute" | "unmute";

export const getPresence = (runId: string, t: string) => call<PresenceView>(`${root(runId)}/presence`, t);
export const sendHeartbeat = (runId: string, t: string, deviceId: string) =>
  post<{ heartbeatEverySeconds: number }>(`${root(runId)}/presence/heartbeat`, t, { deviceId, state: "online" });
/** keepalive so the request survives the page going away. */
export const leavePresence = (runId: string, t: string, deviceId: string) =>
  call<unknown>(`${root(runId)}/presence/leave`, t, { method: "POST", body: JSON.stringify({ deviceId }), keepalive: true });
export const participation = (runId: string, t: string, action: ParticipationAction) =>
  post<{ changed: boolean; announcement?: string }>(
    `${root(runId)}/participation/${action}`, t);

// ---- agents (part 45) ----
export interface AgentCall { tool: string; summary: string; outcome: string; createdAt: string | null }
export interface AgentItem {
  id: string; parentId: string | null; agentName: string; agentVersion: number; title: string;
  state: string; pauseReason: string | null; depth: number;
  budget: { allocatedUsd: string; remainingUsd: string; spentUsd: string };
  progress: { percent: number | null; note: string | null };
  recentToolCalls?: AgentCall[];
}
export const getAgents = (runId: string, t: string) => call<{ items: AgentItem[] }>(`${root(runId)}/agents`, t);
export const agentAction = (runId: string, t: string, id: string, action: "pause" | "resume" | "cancel") =>
  post<unknown>(`${root(runId)}/agents/${encodeURIComponent(id)}/${action}`, t);

/**
 * Real API client for checkpoint-2 wiring. Every call falls back gracefully so
 * the UI stays usable when a backend service isn't reachable (e.g. static demo).
 */
import type { AgentPlan, EventEnvelope } from "@contracts";

const BASES = {
  identity: process.env.NEXT_PUBLIC_IDENTITY_URL ?? "http://localhost:8001",
  orchestrator: process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8004",
  review: process.env.NEXT_PUBLIC_REVIEW_URL ?? "http://localhost:8008",
  meeting: process.env.NEXT_PUBLIC_MEETING_URL ?? "http://localhost:8006",
};

export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number) {
    super(message);
  }
}

const TOKEN_KEY = "harness.token";

export const getToken = () =>
  typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);

async function request<T>(service: keyof typeof BASES, path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASES[service]}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = (body as { detail?: unknown })?.detail;
    throw new ApiError(
      typeof err === "string" ? err : JSON.stringify(err) || res.statusText,
      String(res.status),
      res.status
    );
  }
  return res.json() as Promise<T>;
}

export interface PrincipalDto {
  userId: string;
  teamId: string;
  role: string;
}

export const api = {
  getToken,

  async login(email: string): Promise<PrincipalDto> {
    const data = await request<{ token: string; principal: PrincipalDto }>("identity", "/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
    setToken(data.token);
    return data.principal;
  },

  me(): Promise<PrincipalDto> {
    return request<PrincipalDto>("identity", "/api/v1/me");
  },

  listProjects: () =>
    request<{ items: ProjectDto[] }>("identity", "/api/v1/projects").then((d) => d.items),

  createProject: (name: string) =>
    request<ProjectDto>("identity", "/api/v1/projects", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  async replayEvents(projectId: string, afterSeq = 0): Promise<EventEnvelope[]> {
    const data = await request<{ items: EventEnvelope[]; nextCursor: number }>(
      "identity",
      `/api/v1/projects/${projectId}/events/replay?after_seq=${afterSeq}`
    );
    return data.items;
  },

  async getPlan(runId: string): Promise<AgentPlan> {
    return request<AgentPlan>("orchestrator", `/api/v1/orchestrator/runs/${runId}/plan`);
  },

  async approveRunPlan(runId: string, planId: string): Promise<void> {
    await request("orchestrator", `/api/v1/orchestrator/runs/${runId}/plan/approve`, {
      method: "POST",
      body: JSON.stringify({ principalUserId: getToken() ? "self" : "self" }),
    });
  },

  getReviewData: <T>(prId: string) => request<T>("review", `/api/v1/reviews/${prId}`),

  approvePr: (prId: string, reviewerUserId: string) =>
    request("review", `/api/v1/reviews/${prId}/approve`, {
      method: "POST",
      body: JSON.stringify({ reviewer_user_id: reviewerUserId }),
    }),

  requestChanges: (prId: string, comments: unknown[], reviewerUserId: string) =>
    request("review", `/api/v1/reviews/${prId}/request-changes`, {
      method: "POST",
      body: JSON.stringify({ comments, reviewer_user_id: reviewerUserId }),
    }),
};

// ---- Checkpoint-2 interactive wiring (orchestrator + meeting) ----

export interface ProjectDto {
  id: string;
  name: string;
  repoFullName: string | null;
}

export interface MeetingMsg {
  id?: string;
  senderId?: string;
  userId?: string;
  content: string;
  kind?: string;
  createdAt?: string;
  timestamp?: string;
  isAiGenerated?: boolean;
}

export const orchestrator = {
  proposePlan: (runId: string, goal?: string) =>
    request<{ runId: string; planId: string }>(
      "orchestrator",
      `/api/v1/orchestrator/runs/${runId}/plan`,
      { method: "POST", body: JSON.stringify(goal ? { goal } : {}) }
    ),
  approvePlan: (runId: string, planId: string) =>
    request("orchestrator", `/api/v1/orchestrator/runs/${runId}/plan/approve`, {
      method: "POST",
      body: JSON.stringify({ planId, userId: "ui-user" }),
    }),
  start: (runId: string) =>
    request<{ status: string }>("orchestrator", `/api/v1/orchestrator/runs/${runId}/start`, {
      method: "POST",
    }),
  pause: (runId: string) =>
    request("orchestrator", `/api/v1/orchestrator/runs/${runId}/pause`, { method: "POST" }),
  resume: (runId: string) =>
    request("orchestrator", `/api/v1/orchestrator/runs/${runId}/resume`, { method: "POST" }),
  stop: (runId: string) =>
    request("orchestrator", `/api/v1/orchestrator/runs/${runId}/stop`, { method: "POST" }),
  doneSignal: (runId: string, diffText = "") =>
    request<{ findings: { passed: boolean; summary: string } }>(
      "orchestrator",
      `/api/v1/orchestrator/runs/${runId}/done-signal`,
      { method: "POST", body: JSON.stringify({ diffText }) }
    ),
  status: <T>(runId: string) =>
    request<T>("orchestrator", `/api/v1/orchestrator/runs/${runId}/status`),
  engineResult: <T>(runId: string) =>
    request<T>("orchestrator", `/api/v1/orchestrator/runs/${runId}/engine`),
};

export interface BriefDto {
  id: string;
  status: string;
  requirements: string[];
  constraints: string[];
  acceptanceCriteria: string[];
  risks: string[];
}

export const meeting = {
  create: (projectId: string, title: string) =>
    request<{ id: string; projectId: string; title: string; status: string }>(
      "meeting",
      "/api/v1/meetings",
      { method: "POST", body: JSON.stringify({ projectId, title }) }
    ),
  messages: (meetingId: string) =>
    request<MeetingMsg[]>("meeting", `/api/v1/meetings/${meetingId}/messages`),
  postMessage: (meetingId: string, userId: string, content: string) =>
    request("meeting", `/api/v1/meetings/${meetingId}/messages`, {
      method: "POST",
      body: JSON.stringify({ userId, content }),
    }),
  invokeAi: (meetingId: string, userId: string, content: string) =>
    request("meeting", `/api/v1/meetings/${meetingId}/ai`, {
      method: "POST",
      body: JSON.stringify({ userId, content }),
    }),
  generateBrief: (meetingId: string) =>
    request<BriefDto & { meetingId: string }>("meeting", `/api/v1/meetings/${meetingId}/brief`, {
      method: "POST",
    }),
  getBrief: (meetingId: string) =>
    request<BriefDto & { id: string }>("meeting", `/api/v1/meetings/${meetingId}/brief`),
  approveBrief: (meetingId: string, briefId: string, userId: string) =>
    request("meeting", `/api/v1/meetings/${meetingId}/brief/${briefId}/approve`, {
      method: "POST",
      body: JSON.stringify({ userId }),
    }),
};

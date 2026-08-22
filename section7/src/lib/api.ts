import type { AgentPlan } from "@contracts/plan";
import type { Principal } from "@contracts/roles";
import type { RunStatus } from "@contracts/api";
import type { EventEnvelope } from "@contracts/events";

export const IDENTITY_BASE =
  process.env.NEXT_PUBLIC_IDENTITY_BASE_URL ?? "http://localhost:8001";
export const ORCHESTRATOR_BASE =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_BASE_URL ?? "http://localhost:8004";
export const MEETING_BASE =
  process.env.NEXT_PUBLIC_MEETING_URL ?? "http://localhost:8006";
export const REVIEW_BASE =
  process.env.NEXT_PUBLIC_REVIEW_URL ?? "http://localhost:8008";
export const IDENTITY_WS_BASE =
  process.env.NEXT_PUBLIC_IDENTITY_WS_URL ?? "ws://localhost:8001";

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

interface ApiErrorBody {
  error?: { code?: string; message?: string };
  detail?: { error?: { code?: string; message?: string } } | string | unknown[];
}

/** Pure: turns a non-2xx status + parsed JSON body into a display message + code.
 *  Handles both frozen contract shape {"error": {"code", "message"}} and
 *  FastAPI default envelope {"detail": {"error": {"code", "message"}}}. */
export function parseApiError(status: number, body: unknown): { message: string; code: string } {
  if (!body || typeof body !== "object") {
    return { message: `Request failed with status ${status}`, code: "UNKNOWN" };
  }

  const errBody = body as ApiErrorBody;
  
  // 1. Direct contract error envelope: { error: { code, message } }
  if (errBody.error && typeof errBody.error === "object") {
    const code = errBody.error.code ?? "UNKNOWN";
    const message = errBody.error.message ?? `Request failed with status ${status}`;
    return { message, code };
  }

  // 2. FastAPI wrapped HTTPException detail: { detail: { error: { code, message } } }
  if (errBody.detail && typeof errBody.detail === "object" && !Array.isArray(errBody.detail)) {
    const detailObj = errBody.detail as { error?: { code?: string; message?: string } };
    if (detailObj.error && typeof detailObj.error === "object") {
      const code = detailObj.error.code ?? "UNKNOWN";
      const message = detailObj.error.message ?? `Request failed with status ${status}`;
      return { message, code };
    }
  }

  // 3. FastAPI simple string detail: { detail: "error message" }
  if (typeof errBody.detail === "string") {
    return { message: errBody.detail, code: "UNKNOWN" };
  }

  return { message: `Request failed with status ${status}`, code: "UNKNOWN" };
}

async function apiFetch<T>(url: string, token: string | null, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
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

export async function login(
  email: string,
  teamName?: string,
  teamId?: string,
): Promise<{ token: string; principal: Principal }> {
  return apiFetch(`${IDENTITY_BASE}/api/v1/auth/login`, null, {
    method: "POST",
    body: JSON.stringify({ email, teamName: teamName || undefined, teamId: teamId || undefined }),
  });
}

export function getRunStatus(runId: string, token: string): Promise<RunStatus> {
  return apiFetch(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs/${runId}/status`, token);
}

/** Returns null (not throw) when no plan has been proposed for this run yet. */
export async function getPlan(runId: string, token: string): Promise<AgentPlan | null> {
  try {
    return await apiFetch<AgentPlan>(
      `${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs/${runId}/plan`,
      token,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export function pauseAgent(runId: string, token: string): Promise<{ runId: string; status: string }> {
  return apiFetch(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs/${runId}/pause`, token, {
    method: "POST",
  });
}

export function resumeAgent(runId: string, token: string): Promise<{ runId: string; status: string }> {
  return apiFetch(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs/${runId}/resume`, token, {
    method: "POST",
  });
}

export function stopAgent(runId: string, token: string): Promise<{ runId: string; status: string }> {
  return apiFetch(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs/${runId}/stop`, token, {
    method: "POST",
  });
}

export function submitHumanEdit(
  runId: string,
  path: string,
  content: string,
  token: string,
): Promise<{ runId: string; status: string; path: string }> {
  return apiFetch(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs/${runId}/human-edit`, token, {
    method: "POST",
    body: JSON.stringify({ path, content }),
  });
}

// Section 4's ReviewFindings (harness_orchestrator/reviewer.py) is not part of the
// frozen `contracts` package and its model_dump() emits snake_case keys, unlike every
// other resource body — a real drift from the "camelCase keys" API convention. Typed
// here to match what the endpoint actually returns, not what the convention says it should.
export interface ReviewFindings {
  passed: boolean;
  syntax_ok: boolean;
  conflicts_detected: boolean;
  brief_aligned: boolean;
  summary: string;
  issues: string[];
}

export function signalDone(
  runId: string,
  diffText: string,
  token: string,
): Promise<{ runId: string; findings: ReviewFindings }> {
  return apiFetch(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs/${runId}/done-signal`, token, {
    method: "POST",
    body: JSON.stringify({ diffText }),
  });
}

export function replayEvents(
  projectId: string,
  afterSeq: number,
  token: string,
): Promise<{ items: EventEnvelope[]; nextCursor: number }> {
  return apiFetch(
    `${IDENTITY_BASE}/api/v1/projects/${projectId}/events/replay?after_seq=${afterSeq}`,
    token,
  );
}

/** H8: mint a one-time, 30s, project-bound ticket for the WS upgrade.
 *  Tickets are single-use — every connect attempt needs a fresh one. */
export async function fetchWsTicket(projectId: string, token: string): Promise<string> {
  const body = await apiFetch<{ ticket: string; expiresInSeconds: number }>(
    `${IDENTITY_BASE}/api/v1/ws-ticket`,
    token,
    { method: "POST", body: JSON.stringify({ projectId }) },
  );
  return body.ticket;
}

// ---------------------------------------------------------------------------
// Section 1 — identity resources (projects)
// ---------------------------------------------------------------------------

export interface ProjectDto {
  id: string;
  name: string;
  repoFullName: string | null;
}

export function listProjects(token: string): Promise<ProjectDto[]> {
  return apiFetch<{ items: ProjectDto[]; nextCursor: number }>(
    `${IDENTITY_BASE}/api/v1/projects?limit=100`,
    token,
  ).then((d) => d.items);
}

// ---------------------------------------------------------------------------
// Section 6 — Meeting Area (ported from section9/lib/api.ts, live-proven shapes)
// S6's routes take userId in the body by design (meeting participants are plain
// strings, not session principals); the bearer token still authenticates the call.
// ---------------------------------------------------------------------------

export interface MeetingDto {
  id: string;
  projectId: string;
  title: string;
  status: string;
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

export interface BriefDto {
  id: string;
  status: string;
  requirements: string[];
  constraints: string[];
  acceptanceCriteria: string[];
  risks: string[];
}

function meetingFetch<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  return apiFetch(`${MEETING_BASE}${path}`, token, init);
}

export function createMeeting(projectId: string, title: string, token: string): Promise<MeetingDto> {
  return meetingFetch("/api/v1/meetings", token, {
    method: "POST",
    body: JSON.stringify({ projectId, title }),
  });
}

export function getMessages(meetingId: string, token: string): Promise<MeetingMsg[]> {
  return meetingFetch(`/api/v1/meetings/${meetingId}/messages`, token);
}

export function postMessage(
  meetingId: string,
  userId: string,
  content: string,
  token: string,
): Promise<unknown> {
  return meetingFetch(`/api/v1/meetings/${meetingId}/messages`, token, {
    method: "POST",
    body: JSON.stringify({ userId, content }),
  });
}

export function invokeAi(
  meetingId: string,
  userId: string,
  content: string,
  token: string,
): Promise<{ reply?: string }> {
  return meetingFetch(`/api/v1/meetings/${meetingId}/ai`, token, {
    method: "POST",
    body: JSON.stringify({ userId, content }),
  });
}

export function generateBrief(meetingId: string, token: string): Promise<BriefDto> {
  return meetingFetch(`/api/v1/meetings/${meetingId}/brief`, token, { method: "POST" });
}

export function getBrief(meetingId: string, token: string): Promise<BriefDto> {
  return meetingFetch(`/api/v1/meetings/${meetingId}/brief`, token);
}

export function approveBrief(
  meetingId: string,
  briefId: string,
  userId: string,
  token: string,
): Promise<BriefDto> {
  return meetingFetch(`/api/v1/meetings/${meetingId}/brief/${briefId}/approve`, token, {
    method: "POST",
    body: JSON.stringify({ userId }),
  });
}

// ---------------------------------------------------------------------------
// Section 8 — Review Area
// S8's ApproveRequest/RequestChangesRequest require `reviewer_user_id` in the
// body (route contract, verified against harness_review/api.py). The bearer
// token authenticates; the field records who decided.
// ---------------------------------------------------------------------------

export interface ReviewChangedFile {
  path: string;
  additions: number;
  deletions: number;
}

export interface ReviewRiskFlag {
  path: string;
  severity: string;
  rule: string;
  reason: string;
}

export interface ReviewDto {
  prId: string;
  pr_url: string;
  review_status: string;
  changed_files: ReviewChangedFile[];
  risk_flags?: ReviewRiskFlag[];
  summary?: { sections: { label: string; text: string }[] };
}

function reviewFetch<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  return apiFetch(`${REVIEW_BASE}${path}`, token, init);
}

export function getReviewData(prId: string, token: string): Promise<ReviewDto> {
  return reviewFetch(`/api/v1/reviews/${prId}`, token);
}

export function approvePr(prId: string, reviewerUserId: string, token: string): Promise<unknown> {
  return reviewFetch(`/api/v1/reviews/${prId}/approve`, token, {
    method: "POST",
    body: JSON.stringify({ reviewer_user_id: reviewerUserId }),
  });
}

export function requestChanges(
  prId: string,
  comments: unknown[],
  reviewerUserId: string,
  token: string,
): Promise<unknown> {
  return reviewFetch(`/api/v1/reviews/${prId}/request-changes`, token, {
    method: "POST",
    body: JSON.stringify({ comments, reviewer_user_id: reviewerUserId }),
  });
}

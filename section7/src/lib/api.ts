import type { AgentPlan } from "@contracts/plan";
import type { Principal } from "@contracts/roles";
import type { RunStatus } from "@contracts/api";
import type { EventEnvelope } from "@contracts/events";
// M12-E2: on a 401 the interceptor clears the whole session client-side
// (token + loose harness.* keys) before bouncing to /login?expired=1.
import { clearSession } from "./auth";

export const IDENTITY_BASE =
  process.env.NEXT_PUBLIC_IDENTITY_BASE_URL ?? "http://localhost:8001";
export const ORCHESTRATOR_BASE =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_BASE_URL ?? "http://localhost:8004";
// M12-G8: Section 5 sandbox (tree browser slice) — same convention as the
// other service bases; NEXT_PUBLIC_ is baked at build time like the rest.
export const SANDBOX_BASE =
  process.env.NEXT_PUBLIC_SANDBOX_BASE_URL ?? "http://localhost:8005";
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

// ---------------------------------------------------------------------------
// M12-E2 friendly errors: map machine error codes to human sentences with a
// next-step hint. Keys are the codes sections actually emit today (verified by
// grep across section1–section10); anything unmapped falls through to the
// generic branch. User-facing strings never leak dev jargon (service names,
// ports, storage keys).
// ---------------------------------------------------------------------------
const FRIENDLY_ERROR_MESSAGES: Record<string, string> = {
  RATE_LIMITED:
    "You're doing that too quickly. Please wait a moment and try again.",
  RATE_LIMIT_EXCEEDED:
    "You're doing that too quickly. Please wait a moment and try again.",
  OWNERSHIP_REQUIRED:
    "We need to confirm this workspace belongs to you. Sign in with the email linked to the project.",
  PLAN_NOT_APPROVED:
    "This work can't start until someone approves the plan. Ask your team to review it first.",
  INVALID_STATE:
    "That action doesn't fit where things are right now. Refresh the page and check the run's current step.",
  NO_BRIEF:
    "There's no brief to show yet. Generate one from the meeting before continuing.",
  PLAN_MISMATCH:
    "The plan changed since you last looked. Reload it, then approve or request changes again.",
  PLAN_NOT_FOUND:
    "No plan has been drawn up for this yet. Wait for one to be proposed, then review it.",
  NOT_FOUND:
    "We couldn't find what you asked for. It may have been deleted or is still being created.",
  INVALID_CODE:
    "That code isn't right. Double-check the email and try again.",
  EXPIRED:
    "That code has expired. Request a fresh one to continue.",
  LOCKED:
    "Too many wrong attempts. Please wait a few minutes, then request a new code.",
  ROLE_DENIED:
    "Your role doesn't allow that action. Ask an admin on your team for help.",
  SECURITY_POLICY_VIOLATION:
    "That action was blocked for safety reasons. If you think it's needed, ask your team admin.",
};

/** Pure: returns a user-safe sentence for a known error code; null when the
 *  caller should fall back to the generic message + correlation hint. */
export function friendlyErrorMessage(code: string): string | null {
  return FRIENDLY_ERROR_MESSAGES[code] ?? null;
}

export async function apiFetch<T>(url: string, token: string | null, init?: RequestInit): Promise<T> {
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
    // M12-E2 interceptor: an expired/invalid session is not a normal error —
    // wipe every credential and loose key, bounce to the login page with
    // ?expired=1 (the login page shows a "Session expired" banner for it), and
    // throw ApiError so the awaiting caller still unwinds cleanly.
    if (res.status === 401) {
      clearSession();
      if (typeof window !== "undefined") window.location.href = "/login?expired=1";
      throw new ApiError(
        "Your session has expired. Please sign in again.",
        "UNAUTHORIZED",
        res.status,
      );
    }
    const { message, code } = parseApiError(res.status, body);
    throw new ApiError(friendlyErrorMessage(code) ?? message, code, res.status);
  }
  return body as T;
}

/** M12-E2: human sentence + correlation hint for errors we can't translate.
 *  Used by pages as the last-resort fallback so users always have something to
 *  do next. Known codes get their friendly copy from the map instead. */
export function displayErrorMessage(err: unknown): string {
  if (err instanceof ApiError && friendlyErrorMessage(err.code)) {
    return friendlyErrorMessage(err.code) as string;
  }
  const raw =
    err instanceof Error && err.message.trim() !== "" ? err.message : String(err);
  return `${raw} If this keeps happening, contact support with the time it occurred.`;
}

/** W23: one entry of GET /api/v1/orchestrator/readiness. */
export interface ReadinessCheck {
  check: "provider" | "model" | "worker" | "revision" | "repository" | "permissions";
  status: "ok" | "fail" | "unknown";
  component: string;
  reason: string;
  detail: string;
}

export interface ReadinessReport {
  ok: boolean;
  checks: ReadinessCheck[];
}

export function getReadiness(projectId: string, token: string | null): Promise<ReadinessReport> {
  const q = new URLSearchParams({ projectId });
  return apiFetch(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/readiness?${q}`, token);
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

// M13-G5a: resume answers {runId, state} (RunStateResponse) — real machine
// state, not a static "executing"; terminal runs no-op silently.
export function resumeAgent(runId: string, token: string): Promise<{ runId: string; state: string }> {
  return apiFetch(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs/${runId}/resume`, token, {
    method: "POST",
  });
}

// W2 (N04): retry a stuck run *with a correction*. The order is the feature —
// guidance is recorded first because the executor drains it with
// `guidance_since` when it starts; resuming first would read nothing and throw
// the operator's correction away, which is the fake affordance this replaces.
// Empty content is refused rather than treated as a plain resume: that already
// has its own button, and this one has to mean something.
export async function resumeWithChanges(
  runId: string,
  content: string,
  token: string,
): Promise<{ runId: string; state: string }> {
  const trimmed = content.trim();
  if (!trimmed) {
    throw new ApiError("Describe what should change before resuming", "EMPTY_CHANGE", 400);
  }
  await apiFetch(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs/${runId}/redirect`, token, {
    method: "POST",
    body: JSON.stringify({ kind: "guidance", content: trimmed }),
  });
  return resumeAgent(runId, token);
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
  beforeSeq?: number,
): Promise<{ items: EventEnvelope[]; nextCursor: number }> {
  // W22: beforeSeq pages a truncated replay gap oldest-first.
  const before = beforeSeq ? `&before_seq=${beforeSeq}` : "";
  return apiFetch(
    `${IDENTITY_BASE}/api/v1/projects/${projectId}/events/replay?after_seq=${afterSeq}${before}`,
    token,
  );
}

// ---------------------------------------------------------------------------
// Section 5 — sandbox checkpoints (M12-G9). List is a JSON resource; the diff
// endpoint returns raw text/plain (not the JSON envelope apiFetch assumes),
// so it gets a dedicated fetcher that still reuses the auth header shape.
// SANDBOX_BASE is declared once at the top of this file (G8 tree browser).
// ---------------------------------------------------------------------------

export interface CheckpointDto {
  sha: string;
  message: string;
  createdAt: string;
  taskId: string;
}

export interface CheckpointList {
  projectId: string;
  items: CheckpointDto[];
  nextCursor: string | null;
}

export function listCheckpoints(
  projectId: string,
  token: string,
): Promise<CheckpointList> {
  return apiFetch(
    `${SANDBOX_BASE}/api/v1/sandbox/${encodeURIComponent(projectId)}/checkpoints`,
    token,
  );
}

export async function getCheckpointDiff(
  projectId: string,
  sha: string,
  token: string,
): Promise<string> {
  const res = await fetch(
    `${SANDBOX_BASE}/api/v1/sandbox/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(sha)}/diff`,
    { headers: { authorization: `Bearer ${token}` } },
  );
  if (!res.ok) throw new ApiError(`Diff fetch failed (${res.status})`, "UNKNOWN", res.status);
  return res.text();
}

// ---------------------------------------------------------------------------
// C16: hunk-level review. The server parses the snapshot diff; callers send
// only (path, index) pairs back, never patch text.
// ---------------------------------------------------------------------------

export interface CheckpointHunk {
  index: number;
  header: string;
  lines: string[];
  additions: number;
  deletions: number;
}

export interface CheckpointFile {
  path: string;
  binary: boolean;
  hunks: CheckpointHunk[];
}

export interface CheckpointReview {
  projectId: string;
  sha: string;
  files: CheckpointFile[];
}

export async function listCheckpointHunks(
  projectId: string,
  sha: string,
  token: string,
): Promise<CheckpointReview> {
  return apiFetch(
    `${SANDBOX_BASE}/api/v1/sandbox/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(sha)}/hunks`,
    token,
  );
}

export async function revertCheckpointHunks(
  projectId: string,
  sha: string,
  hunks: Array<{ path: string; index: number }>,
  token: string,
): Promise<{ status: string; sha: string; reverted: number }> {
  return apiFetch(
    `${SANDBOX_BASE}/api/v1/sandbox/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(sha)}/revert`,
    token,
    { method: "POST", body: JSON.stringify({ hunks }) },
  );
}

export async function restoreCheckpoint(
  projectId: string,
  sha: string,
  token: string,
): Promise<{ status: string; sha: string; restoredTo: string }> {
  return apiFetch(
    `${SANDBOX_BASE}/api/v1/sandbox/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(sha)}/restore`,
    token,
    { method: "POST", body: "{}" },
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
// Section 1 — auth (H2r ownership flow)
// ---------------------------------------------------------------------------

/** Legacy passwordless login. When the deployment sets HARNESS_REQUIRE_OWNERSHIP,
 *  this returns 403 OWNERSHIP_REQUIRED and callers must fall back to the OTP flow. */
export function login(
  email: string,
  teamName?: string,
): Promise<{ token: string; principal: Principal }> {
  return apiFetch(`${IDENTITY_BASE}/api/v1/auth/login`, null, {
    method: "POST",
    body: JSON.stringify({ email, teamName: teamName || undefined }),
  });
}

export interface PrincipalDto {
  userId: string;
  teamId: string;
  role: string;
}

/** H2r step 1: email a single-use 6-digit code (enumeration-safe — always ok:true). */
export async function requestLoginOtp(email: string): Promise<void> {
  await apiFetch(`${IDENTITY_BASE}/api/v1/auth/request-login`, null, {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

/** H2r step 2: swap code for a session; provisions identically to legacy login. */
export function verifyLoginOtp(
  email: string,
  code: string,
): Promise<{ token: string; principal: PrincipalDto }> {
  return apiFetch(`${IDENTITY_BASE}/api/v1/auth/verify-otp`, null, {
    method: "POST",
    body: JSON.stringify({ email, code }),
  });
}

/** F-gap closure (external-validation §SSO): start — mints state/PKCE
 *  server-side and returns the IdP authorization URL the browser must be
 *  sent to. Unconfigured teams answer 404 SSO_NOT_CONFIGURED (the caller
 *  falls back to email sign-in honestly). */
export function ssoStart(
  teamId: string,
  redirectUri: string,
): Promise<{ authorizationUrl: string }> {
  return apiFetch(`${IDENTITY_BASE}/api/v1/auth/sso/start`, null, {
    method: "POST",
    body: JSON.stringify({ teamId, redirectUri }),
  });
}

/** F-gap closure (external-validation §SSO): complete — swaps code+state for
 *  a session. redirectUri must be byte-identical to the one bound at start
 *  (lib/sso.ts stashes it; mismatches answer 400 SSO_STATE_INVALID). */
export function ssoComplete(
  teamId: string,
  code: string,
  state: string,
  redirectUri: string,
): Promise<{ token: string; principal: Principal }> {
  return apiFetch(`${IDENTITY_BASE}/api/v1/auth/sso/complete`, null, {
    method: "POST",
    body: JSON.stringify({ teamId, code, state, redirectUri }),
  });
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
  /** M16-X2: 1-based transcript position on GET /messages page items —
   *  the preferred pagination cursor (`before=<messageSeq>`). */
  messageSeq?: number;
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

// M16-X2: S6's GET messages body is now the pagination envelope
// {items, hasMore} (server default limit 200, cap 500). MeetingMsg keeps its
// optional fields so both the live S6 shape and legacy fixtures typecheck.
export interface MessagePage {
  items: MeetingMsg[];
  hasMore: boolean;
}

/**
 * Raw paginated fetch (M16-X2): `before` is the cursor — messages strictly
 * OLDER than that message; omit it for the newest window. Prefer the stable
 * `messageSeq` from a page item; legacy message ids are accepted by the
 * server for one release. `limit` defaults server-side to
 * MESSAGES_DEFAULT_LIMIT and is hard-capped at 500.
 */
export function getMessagePage(
  meetingId: string,
  token: string,
  opts?: { limit?: number; before?: string | number },
): Promise<MessagePage> {
  const qs = new URLSearchParams();
  if (opts?.limit !== undefined) qs.set("limit", String(opts.limit));
  if (opts?.before !== undefined) qs.set("before", String(opts.before));
  const query = qs.toString();
  return meetingFetch(
    `/api/v1/meetings/${meetingId}/messages${query ? `?${query}` : ""}`,
    token,
  );
}

/** Convenience read: full page fetch with the older tail unwrapped. */
export async function getMessages(
  meetingId: string,
  token: string,
): Promise<MeetingMsg[]> {
  return (await getMessagePage(meetingId, token)).items;
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

// M13-G5b: the old `invokeAi` export is GONE. The single-call send path is
// `sendWithAi` in lib/meetingSend.ts (S6 replies server-side; the client's
// second POST to /ai was a duplicate that raced it).

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

export interface ReviewTestResult {
  name: string;
  status: string;
  pass_count: number;
  fail_count: number;
  summary: string;
}

export interface ReviewDto {
  prId: string;
  pr_url: string;
  /** Section 8 also sends camelCase; older/other surfaces used snake_case.
   *  Both stay optional so the verdict card can tell "absent" from "empty". */
  prUrl?: string;
  /** Unified diff text of the PR (section8 ReviewDataResponse.diff). */
  diff?: string;
  changed_files?: ReviewChangedFile[];
  changedFiles?: ReviewChangedFile[];
  risk_flags?: ReviewRiskFlag[];
  riskFlags?: ReviewRiskFlag[];
  testResults?: ReviewTestResult[];
  approvedBy?: string | null;
  review_status?: string;
  summary?: { sections: { label: string; text: string }[] };
}

function reviewFetch<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  return apiFetch(`${REVIEW_BASE}${path}`, token, init);
}

export function getReviewData(prId: string, token: string): Promise<ReviewDto> {
  return reviewFetch(`/api/v1/reviews/${prId}`, token);
}

/** `viewedDetail`: was the diff on screen before Approve (X-07 KPI). Omit when not measured. */
export function approvePr(
  prId: string, reviewerUserId: string, token: string, viewedDetail?: boolean,
): Promise<unknown> {
  return reviewFetch(`/api/v1/reviews/${prId}/approve`, token, {
    method: "POST",
    body: JSON.stringify({ reviewer_user_id: reviewerUserId, viewed_detail: viewedDetail }),
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

export interface ReviewComment {
  id: string;
  file_path: string | null;
  line: number | null;
  body: string;
}

export function listReviewComments(prId: string, token: string): Promise<ReviewComment[]> {
  return reviewFetch(`/api/v1/reviews/${prId}/comments`, token);
}

export function addReviewComment(
  prId: string,
  comment: { file_path: string; line: number | null; body: string },
  token: string,
): Promise<ReviewComment> {
  return reviewFetch(`/api/v1/reviews/${prId}/comments`, token, {
    method: "POST",
    body: JSON.stringify(comment),
  });
}

// ---------------------------------------------------------------- github (S2)

export const GITHUB_BASE =
  process.env.NEXT_PUBLIC_GITHUB_URL ?? "http://localhost:8002";

export interface GithubConnection {
  connected: boolean;
  login: string | null;
  scopes: string[];
}

export function getGithubConnection(token: string): Promise<GithubConnection> {
  return apiFetch(`${GITHUB_BASE}/api/v1/github/connection`, token);
}

/** Mints a one-time start-ticket, then points the browser at /oauth/start.
 *  Navigation cannot carry Authorization headers — the ticket is the hop. */
export async function connectGithub(token: string): Promise<void> {
  const { ticket } = await apiFetch<{ ticket: string }>(
    `${GITHUB_BASE}/api/v1/github/oauth/start-ticket`,
    token,
    { method: "POST" },
  );
  window.location.href = `${GITHUB_BASE}/api/v1/github/oauth/start?ticket=${encodeURIComponent(ticket)}`;
}

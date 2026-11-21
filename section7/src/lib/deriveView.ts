import { EventType, type EventEnvelope } from "@contracts/events";

export interface LastReview {
  passed: boolean;
  summary: string;
  issues: string[];
  seq: number;
}

/** A shell-gate denial asking a person to widen the run's scope. `grants` is
 *  always false: the request itself grants nothing (loop_guard.scope_request_of). */
export interface ScopeRequestView {
  path: string;
  grants: false;
}

/** One tool invocation, paired from its tool_call and tool_result events.
 *  `ok` stays null while the call is still running — the UI says "running",
 *  it does not guess success. */
export interface ToolCallView {
  callId: string;
  name: string;
  arguments: string;
  ok: boolean | null;
  error: string | null;
  output: string;
  durationMs: number | null;
  /** Present only on a tool_result carrying a scopeRequest. */
  scopeRequest: ScopeRequestView | null;
}

function parseScopeRequest(payload: Record<string, unknown>): ScopeRequestView | null {
  const raw = payload.scopeRequest;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const rec = raw as Record<string, unknown>;
  if (typeof rec.path !== "string" || rec.path === "" || rec.grants !== false) return null;
  return { path: rec.path, grants: false };
}

export interface GoalImagesView {
  /** Image content blocks the model was actually handed. */
  attached: number;
  /** Images that existed for this run but did not travel, with the reason. */
  skipped: { artifactId: string; name: string; reason: string }[];
  /** Set when the deployment refused images entirely (e.g. model has no vision). */
  refused: string | null;
}

export interface WorkingAreaDerivedView {
  blockerQuestion: string | null;
  lastReview: LastReview | null;
  terminalLines: string[];
  /** Files the agent has touched, in first-seen order (diff_progress events). */
  changedPaths: string[];
  /** Tool calls in call order, each paired with its result when one arrived. */
  toolCalls: ToolCallView[];
  /** The window the runtime runs with, as reported at execution start. */
  contextWindow: number | null;
  /** Tokens summarised away by compaction so far (summed across compactions). */
  compactedTokens: number;
  /** What the model was shown besides text, or null when no attempt reported. */
  goalImages: GoalImagesView | null;
}

const TERMINAL_LINE_CAP = 200;

/** Pure: walks the (already seq-ordered) event stream to derive what the status panel
 *  shows. Blocker clears once a resume/execution-started event supersedes it. The last
 *  review result stays visible even after the state machine has already moved back to
 *  "executing" — otherwise the pass/fail result would flash and disappear.
 *  M2: terminal_output lines accumulate into a capped rolling buffer for the
 *  TerminalPanel (dsh rule — model-visible work arrives as events).
 *  M-A1: diff_progress carries `{ path, checkpoint? }` per applied edit batch, so the
 *  diff panel can name the files already touched instead of claiming there is no feed. */
export function deriveWorkingAreaView(events: EventEnvelope[]): WorkingAreaDerivedView {
  let blockerQuestion: string | null = null;
  let lastReview: LastReview | null = null;
  const terminalLines: string[] = [];
  const changedPaths: string[] = [];
  const toolCalls: ToolCallView[] = [];
  const byCallId = new Map<string, ToolCallView>();
  const toolCallStart = new Map<string, number>();
  let contextWindow: number | null = null;
  let compactedTokens = 0;
  let goalImages: GoalImagesView | null = null;

  for (const evt of events) {
    const payload = evt.payload ?? {};
    if (evt.type === EventType.BLOCKED_AWAITING_CLARIFICATION) {
      blockerQuestion = (payload.question as string) ?? "Agent is awaiting clarification.";
    } else if (evt.type === EventType.RESUMED || evt.type === EventType.EXECUTION_STARTED) {
      blockerQuestion = null;
      if (evt.type === EventType.EXECUTION_STARTED && typeof payload.contextWindow === "number") {
        contextWindow = payload.contextWindow;
      }
    } else if (evt.type === EventType.REVIEW_PASSED || evt.type === EventType.REVIEW_FAILED) {
      const findings = (payload.findings ?? {}) as Record<string, unknown>;
      lastReview = {
        passed: evt.type === EventType.REVIEW_PASSED,
        summary: (findings.summary as string) ?? "",
        issues: (findings.issues as string[]) ?? [],
        seq: evt.seq,
      };
    } else if (evt.type === EventType.TERMINAL_OUTPUT) {
      const line = payload.line;
      if (typeof line === "string") {
        terminalLines.push(line);
        if (terminalLines.length > TERMINAL_LINE_CAP) {
          terminalLines.splice(0, terminalLines.length - TERMINAL_LINE_CAP);
        }
      }
    } else if (evt.type === EventType.CONTEXT_COMPACTED) {
      if (typeof payload.contextWindow === "number") contextWindow = payload.contextWindow;
      const tokens = payload.compactedTokens;
      if (typeof tokens === "number" && tokens > 0) compactedTokens += tokens;
    } else if (evt.type === EventType.GOAL_IMAGES) {
      const attached = typeof payload.attached === "number" ? payload.attached : 0;
      const skipped = Array.isArray(payload.skipped) ? payload.skipped : [];
      const refused = typeof payload.refused === "string" ? payload.refused : null;
      // A later attempt that reports nothing does not erase an earlier one:
      // a resumed run legitimately sends no new image, and showing "no images"
      // after the user's screenshot was already in the session would be a lie.
      if (attached > 0 || skipped.length > 0 || refused !== null) {
        goalImages = {
          attached,
          skipped: skipped
            .filter((s): s is Record<string, unknown> => typeof s === "object" && s !== null)
            .map((s) => ({
              artifactId: typeof s.artifactId === "string" ? s.artifactId : "",
              name: typeof s.name === "string" ? s.name : "image",
              reason: typeof s.reason === "string" ? s.reason : "not sent",
            })),
          refused,
        };
      }
    } else if (evt.type === EventType.TOOL_CALL) {
      const callId = typeof payload.callId === "string" ? payload.callId : "";
      if (callId && !byCallId.has(callId)) {
        const call: ToolCallView = {
          callId,
          name: typeof payload.name === "string" && payload.name ? payload.name : "tool",
          arguments: typeof payload.arguments === "string" ? payload.arguments : "",
          ok: null,
          error: null,
          output: "",
          durationMs: null,
          scopeRequest: null,
        };
        byCallId.set(callId, call);
        toolCalls.push(call);
        if (typeof payload.startedAt === "number") toolCallStart.set(callId, payload.startedAt);
      }
    } else if (evt.type === EventType.TOOL_RESULT) {
      const callId = typeof payload.callId === "string" ? payload.callId : "";
      const call = callId ? byCallId.get(callId) : undefined;
      if (call) {
        call.ok = payload.ok === true;
        call.error = typeof payload.error === "string" ? payload.error : null;
        call.output = typeof payload.output === "string" ? payload.output : "";
        call.scopeRequest = parseScopeRequest(payload);
        const started = toolCallStart.get(callId);
        const finished = typeof payload.finishedAt === "number" ? payload.finishedAt : null;
        call.durationMs =
          typeof started === "number" && finished !== null && finished >= started
            ? finished - started
            : null;
      }
    } else if (evt.type === EventType.DIFF_PROGRESS) {
      const path = payload.path;
      if (typeof path === "string" && path && !changedPaths.includes(path)) {
        changedPaths.push(path);
      }
    }
  }

  return {
    blockerQuestion,
    lastReview,
    terminalLines,
    changedPaths,
    toolCalls,
    contextWindow,
    compactedTokens,
    goalImages,
  };
}

// U2 timeline slice — pure rules for the run workspace (docs/plans/ui-overhaul-plan-2026-11-13.md §5).
// Events come from Section 1's replay/bus; this module only presents recorded facts. It pairs a
// tool_call with its tool_result, derives the run state visible at a selected step, and owns the
// scrub/follow rules so they can be tested without a browser. Raw payloads (diffs, logs, env values)
// never leave this file: receipts are short, human summaries only.
import { scrubSecrets } from "@/lib/redact";
import { stepLabel } from "../../app/runs/runsApi";

/** Narrow shape of a contract EventEnvelope, so tests can build synthetic events. */
export interface TimelineEventInput {
  seq: number;
  type: string;
  ts: string | null;
  payload?: Record<string, unknown> | null;
}

export type StepStatus = "running" | "ok" | "failed" | "neutral";

export interface TimelineStep {
  /** Stable React key: call id for tool pairs, sequence for single events. */
  id: string;
  /** First sequence number in the block. */
  seq: number;
  /** Last sequence number in the block (same as seq for single events). */
  endSeq: number;
  type: string;
  label: string;
  ts: string | null;
  status: StepStatus;
  /** One short line: file path, command, result. Never a raw diff/log/env. */
  receipt: string | null;
}

export interface StateAtStep {
  /** AgentState the run was in after this step, or null when no event records one. */
  state: string | null;
  /** Sequence of the event that set the state, for an auditable label. */
  seq: number | null;
  /** Human label of that event, or null. */
  label: string | null;
}

const MAX_RECEIPT = 120;

// Every payload string is scrubbed on the way in (before shortCommand drops words such as
// "Bearer", which would leave a bare token no pattern recognises) and again on the way out.
function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? scrubSecrets(value.trim()) : null;
}

function truncate(value: string, max: number = MAX_RECEIPT): string {
  const flat = scrubSecrets(value).replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** Drop leading KEY=value assignments and bearer-looking tokens, then flatten to one line. */
export function shortCommand(command: string): string {
  const lines = command
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  // A multi-line call often starts with env assignments; use the first line
  // that still has a real command word after them.
  const first =
    lines.find((line) =>
      line.split(/\s+/).some((t) => !/^[A-Za-z_][A-Za-z0-9_]*=/.test(t)),
    ) ??
    lines[0] ??
    "";
  const tokens = first
    .split(/\s+/)
    .filter((t) => t && !/^[A-Za-z_][A-Za-z0-9_]*=/.test(t))
    .filter((t) => !/^Bearer$/i.test(t));
  return truncate(tokens.join(" ") || "(command not shown)");
}

function payloadRecord(payload: Record<string, unknown> | null | undefined): Record<string, unknown> | null {
  return payload && typeof payload === "object" ? payload : null;
}

function argsRecord(payload: Record<string, unknown> | null): Record<string, unknown> | null {
  const args = payload?.arguments;
  if (args && typeof args === "object" && !Array.isArray(args)) return args as Record<string, unknown>;
  if (typeof args === "string") {
    try {
      const parsed: unknown = JSON.parse(args);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return null;
    }
  }
  return null;
}

const PATH_KEYS = ["path", "file_path", "filePath", "file", "target"] as const;
const COMMAND_KEYS = ["command", "cmd"] as const;

/** Short receipt for a tool call: path first (the least revealing useful fact), then command. */
export function toolCallReceipt(payload: Record<string, unknown> | null | undefined): string | null {
  const rec = payloadRecord(payload);
  if (!rec) return null;
  const name = text(rec.name);
  const args = argsRecord(rec);
  if (args) {
    for (const key of PATH_KEYS) {
      const p = text(args[key]);
      if (p) return truncate(`path: ${p}`);
    }
    for (const key of COMMAND_KEYS) {
      const c = text(args[key]);
      if (c) return truncate(`$ ${shortCommand(c)}`);
    }
  }
  return name ? truncate(`tool: ${name}`) : null;
}

/** Short receipt for a tool result: ok/failed plus a one-line reason, never the output body. */
export function toolResultReceipt(payload: Record<string, unknown> | null | undefined): string | null {
  const rec = payloadRecord(payload);
  if (!rec) return null;
  const ok = rec.ok === true;
  const duration =
    typeof rec.durationMs === "number" && Number.isFinite(rec.durationMs)
      ? ` · ${Math.max(0, Math.round(rec.durationMs))} ms`
      : "";
  if (ok) return `ok${duration}`;
  const error = text(rec.error);
  return truncate(`failed${error ? `: ${error.split(/\r?\n/)[0]}` : " (no error text recorded)"}${duration}`);
}

function eventReceipt(event: TimelineEventInput, payload: Record<string, unknown> | null): string | null {
  switch (event.type) {
    case "tool_call":
      return toolCallReceipt(payload);
    case "tool_result":
      return toolResultReceipt(payload);
    case "plan_proposed": {
      const steps = payload?.steps;
      const n = Array.isArray(steps) ? steps.length : typeof payload?.stepCount === "number" ? payload.stepCount : null;
      return n === null ? null : `${n} proposed step${n === 1 ? "" : "s"}`;
    }
    case "diff_progress":
    case "terminal_output":
      // The body is deliberately not shown: the run page links to evidence instead.
      return event.type === "diff_progress" ? "diff updated" : "terminal output recorded";
    case "pr_opened": {
      const url = text(payload?.url);
      return url ? truncate(url) : null;
    }
    default:
      return null;
  }
}

function statusFor(type: string, payload: Record<string, unknown> | null): StepStatus {
  if (type === "tool_call") return "running";
  if (type === "tool_result") return payload?.ok === true ? "ok" : "failed";
  if (/fail|error|abandon/i.test(type)) return "failed";
  if (/approved|passed|complete|done|resumed/i.test(type)) return "ok";
  return "neutral";
}

function callIdOf(payload: Record<string, unknown> | null): string | null {
  return text(payload?.callId);
}

/** One step of the run aggregate (GET /orchestrator/runs/{id}): run-scoped, newest 100, with a
 *  server-scrubbed `detail` instead of the raw payload. */
export interface AggregateStepInput {
  seq: number;
  type: string;
  ts: string | null;
  reason?: string | null;
  detail?: Record<string, string | number | boolean> | null;
}

/** Rebuild the narrow payload the timeline rules read from the aggregate's step detail. */
export function stepsToEvents(steps: AggregateStepInput[]): TimelineEventInput[] {
  return steps.map((s) => {
    const d = s.detail ?? {};
    const payload: Record<string, unknown> = { ...d };
    if (s.reason) payload.reason = s.reason;
    if (typeof d.path === "string" || typeof d.command === "string") {
      payload.arguments = { path: d.path, command: d.command };
    }
    return { seq: s.seq, type: s.type, ts: s.ts, payload };
  });
}

/** Group raw events (oldest→newest) into timeline blocks; a tool_call/result pair is one block. */
export function groupEvents(events: TimelineEventInput[]): TimelineStep[] {
  const ordered = events
    .filter((e) => e && typeof e.type === "string" && Number.isFinite(e.seq))
    .slice()
    .sort((a, b) => a.seq - b.seq);
  const blocks: TimelineStep[] = [];
  const openCalls = new Map<string, number>();

  for (const event of ordered) {
    const payload = payloadRecord(event.payload);
    const callId = callIdOf(payload);

    if (event.type === "tool_result" && callId && openCalls.has(callId)) {
      const index = openCalls.get(callId)!;
      const block = blocks[index];
      blocks[index] = {
        ...block,
        endSeq: event.seq,
        ts: block.ts ?? event.ts,
        status: statusFor(event.type, payload),
        receipt: toolResultReceipt(payload) ?? block.receipt,
        label: stepLabel("tool_result"),
      };
      openCalls.delete(callId);
      continue;
    }

    const block: TimelineStep = {
      id: callId && event.type === "tool_call" ? `call:${callId}` : `seq:${event.seq}`,
      seq: event.seq,
      endSeq: event.seq,
      type: event.type,
      label: stepLabel(event.type, text(payload?.reason) ?? undefined),
      ts: event.ts,
      status: statusFor(event.type, payload),
      receipt: eventReceipt(event, payload),
    };
    blocks.push(block);
    if (event.type === "tool_call" && callId) openCalls.set(callId, blocks.length - 1);
  }

  return blocks;
}

/** Normalize dotted legacy names (`plan.proposed`) to the contract's underscore names. */
function normalizeType(type: string): string {
  return type.replace(/\./g, "_");
}

const STATE_AFTER: Record<string, string> = {
  plan_proposed: "awaiting_plan_approval",
  plan_approved: "executing",
  execution_started: "executing",
  paused: "paused",
  resumed: "executing",
  blocked_awaiting_clarification: "blocked",
  done_signal_received: "reviewing",
  review_passed: "pr_opened",
  review_failed: "blocked",
  pr_opened: "pr_opened",
  engine_completed: "awaiting_done_signal",
  engine_failed: "blocked",
  run_abandoned: "stopped",
};

/** The run state recorded by the selected step or the nearest earlier state event. */
export function stateAtStep(steps: TimelineStep[], index: number): StateAtStep {
  if (steps.length === 0) return { state: null, seq: null, label: null };
  const last = Math.min(Math.max(index, 0), steps.length - 1);
  for (let i = last; i >= 0; i -= 1) {
    const step = steps[i];
    const state = STATE_AFTER[normalizeType(step.type)];
    if (state) return { state, seq: step.seq, label: step.label };
  }
  return { state: null, seq: null, label: null };
}

export type FollowAction = "manual-move" | "toggle" | "new-step";

/** Follow stays on for stream arrivals, turns off on a manual move, toggles on the switch. */
export function reduceFollow(following: boolean, action: FollowAction): boolean {
  if (action === "manual-move") return false;
  if (action === "toggle") return !following;
  return following;
}

/** Keyboard scrubber: j/ArrowDown forward, k/ArrowUp back, Home/End jump. Clamps to [0, n-1]. */
export function scrubIndex(index: number, length: number, key: string): number {
  if (length <= 0) return -1;
  const last = length - 1;
  const current = Math.min(Math.max(index, 0), last);
  switch (key) {
    case "j":
    case "ArrowDown":
      return Math.min(last, current + 1);
    case "k":
    case "ArrowUp":
      return Math.max(0, current - 1);
    case "Home":
      return 0;
    case "End":
      return last;
    default:
      return current;
  }
}

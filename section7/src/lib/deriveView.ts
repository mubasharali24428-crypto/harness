import { EventType, type EventEnvelope } from "@contracts/events";

export interface LastReview {
  passed: boolean;
  summary: string;
  issues: string[];
  seq: number;
}

export interface WorkingAreaDerivedView {
  blockerQuestion: string | null;
  lastReview: LastReview | null;
  terminalLines: string[];
  /** Files the agent has touched, in first-seen order (diff_progress events). */
  changedPaths: string[];
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

  for (const evt of events) {
    const payload = evt.payload ?? {};
    if (evt.type === EventType.BLOCKED_AWAITING_CLARIFICATION) {
      blockerQuestion = (payload.question as string) ?? "Agent is awaiting clarification.";
    } else if (evt.type === EventType.RESUMED || evt.type === EventType.EXECUTION_STARTED) {
      blockerQuestion = null;
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
    } else if (evt.type === EventType.DIFF_PROGRESS) {
      const path = payload.path;
      if (typeof path === "string" && path && !changedPaths.includes(path)) {
        changedPaths.push(path);
      }
    }
  }

  return { blockerQuestion, lastReview, terminalLines, changedPaths };
}

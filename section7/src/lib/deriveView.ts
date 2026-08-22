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
}

const TERMINAL_LINE_CAP = 200;

/** Pure: walks the (already seq-ordered) event stream to derive what the status panel
 *  shows. Blocker clears once a resume/execution-started event supersedes it. The last
 *  review result stays visible even after the state machine has already moved back to
 *  "executing" — otherwise the pass/fail result would flash and disappear.
 *  M2: terminal_output lines accumulate into a capped rolling buffer for the
 *  TerminalPanel (dsh rule — model-visible work arrives as events). */
export function deriveWorkingAreaView(events: EventEnvelope[]): WorkingAreaDerivedView {
  let blockerQuestion: string | null = null;
  let lastReview: LastReview | null = null;
  const terminalLines: string[] = [];

  for (const evt of events) {
    if (evt.type === EventType.BLOCKED_AWAITING_CLARIFICATION) {
      blockerQuestion = (evt.payload.question as string) ?? "Agent is awaiting clarification.";
    } else if (evt.type === EventType.RESUMED || evt.type === EventType.EXECUTION_STARTED) {
      blockerQuestion = null;
    } else if (evt.type === EventType.REVIEW_PASSED || evt.type === EventType.REVIEW_FAILED) {
      const findings = (evt.payload.findings ?? {}) as Record<string, unknown>;
      lastReview = {
        passed: evt.type === EventType.REVIEW_PASSED,
        summary: (findings.summary as string) ?? "",
        issues: (findings.issues as string[]) ?? [],
        seq: evt.seq,
      };
    } else if (evt.type === EventType.TERMINAL_OUTPUT) {
      const line = evt.payload.line;
      if (typeof line === "string") {
        terminalLines.push(line);
        if (terminalLines.length > TERMINAL_LINE_CAP) {
          terminalLines.splice(0, terminalLines.length - TERMINAL_LINE_CAP);
        }
      }
    }
  }

  return { blockerQuestion, lastReview, terminalLines };
}

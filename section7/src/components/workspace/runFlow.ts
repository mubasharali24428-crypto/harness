// R3 pure rules: what the composer does with a draft, and what the run asks of
// a person when it stops. No fetches, no DOM, so vitest covers them in node.

export type ComposerMode = "goal" | "plan" | "direction" | "finished";

const LIVE = new Set(["planning", "executing", "paused", "blocked", "awaiting_done_signal", "reviewing"]);

/** What the composer is for, given the run's state (null = no run yet). */
export function composerMode(state: string | null): ComposerMode {
  if (state === null) return "goal";
  if (state === "awaiting_plan_approval") return "plan";
  return LIVE.has(state) ? "direction" : "finished";
}

export const COMMANDS = [
  { name: "pause", hint: "Pause the agent", ok: (s: string) => s === "executing", why: "the run is not executing" },
  { name: "resume", hint: "Resume a paused run", ok: (s: string) => s === "paused" || s === "blocked", why: "the run is not paused" },
  { name: "stop", hint: "Stop the run", ok: (s: string) => LIVE.has(s), why: "the run is not active" },
] as const;

export type CommandName = (typeof COMMANDS)[number]["name"];

export type ComposerAction =
  | { kind: "empty" }
  | { kind: "text"; text: string }
  | { kind: "command"; name: CommandName }
  | { kind: "error"; message: string };

/** A draft is a slash command only when it starts with "/"; anything else is text. */
export function parseDraft(draft: string, state: string | null): ComposerAction {
  const text = draft.trim();
  if (!text) return { kind: "empty" };
  if (!text.startsWith("/")) return { kind: "text", text };
  const name = text.slice(1).split(/\s+/, 1)[0].toLowerCase();
  const command = COMMANDS.find((c) => c.name === name);
  if (!command) {
    return { kind: "error", message: `Unknown command /${name}. Available: ${COMMANDS.map((c) => `/${c.name}`).join(", ")}.` };
  }
  if (state === null || !command.ok(state)) {
    return { kind: "error", message: `/${command.name} does nothing now: ${state === null ? "there is no run yet" : command.why}.` };
  }
  return { kind: "command", name: command.name };
}

export interface NextStep {
  recap: string;
  question: string;
  action: "diff" | "resume" | "direction" | "new";
  label: string;
}

/** The box shown when a run stops moving: a short recap, one question, one button. */
export function nextStep(state: string): NextStep | null {
  switch (state) {
    case "pr_opened":
      return { recap: "The agent finished its work and opened a pull request. Nothing merges without a person.",
        question: "Review the changes?", action: "diff", label: "Open the diff" };
    case "paused":
      return { recap: "The run is paused. The agent cannot change files until it is resumed.",
        question: "Resume the run?", action: "resume", label: "Resume" };
    case "blocked":
      return { recap: "The run is blocked. It will not continue until a person decides what happens next.",
        question: "Tell the agent how to continue?", action: "direction", label: "Write direction" };
    case "done":
    case "completed":
    case "stopped":
    case "cancelled":
    case "failed":
      return { recap: state === "failed" ? "The run failed. The steps above show how far it got."
          : state === "stopped" || state === "cancelled" ? "The run was stopped. The agent can no longer change files."
          : "The run has finished.",
        question: "Start another run?", action: "new", label: "New run" };
    default:
      return null;
  }
}

/** One item per non-empty line: how the plan editor's text areas map to lists. */
export function linesToList(text: string): string[] {
  return text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
}

"use client";

// R3 composer: the one input on a run. With no run it takes a goal and proposes
// a plan; on a live run it sends direction to the agent or runs a slash command;
// before approval and after the run ends it is disabled and says why. Every
// action is an existing API call (plan gate, redirect, pause/resume/stop).

import * as React from "react";
import { useRouter } from "next/navigation";
import { stopAgent } from "@/lib/api";
import { getProjectId, setProjectId, setRunId } from "@/lib/auth";
import { loadErrorMessage } from "@/lib/humanize";
import { ModelPrefsForm } from "@/components/ModelPrefs";
import { stopNotice } from "@/components/Controls";
import { Button } from "@/components/ui";
import { proposePlan } from "../../../app/dashboard/planGateApi";
import { pauseOrResumeRun } from "../../../app/runs/runsApi";
import { getHistory, joinRun, sendRedirect } from "../../../app/projects/[projectId]/runs/[runId]/collabApi";
import { takesEffectText } from "../../../app/projects/[projectId]/runs/[runId]/collabModel";
import { RefAutocomplete } from "../../../app/meeting/RefAutocomplete";
import { COMMANDS, composerMode, parseDraft } from "./runFlow";

export const COMPOSER_INPUT_ID = "ws-composer-input";

interface SentDirection {
  id: string;
  text: string;
  /** What the server said about when the agent reads it. */
  status: string;
  /** Collab sequence of the direction; null when queued or not reported. */
  seq: number | null;
  delivered?: boolean;
}

const PLACEHOLDER = {
  goal: "Describe what the agent should do. Enter proposes a plan; nothing runs before you approve it.",
  direction: "Send direction to the agent, or type / for commands and @ for a file.",
  plan: "",
  finished: "",
} as const;

const DISABLED_REASON = {
  plan: "Approve or edit the plan above first. Direction opens once the run starts.",
  finished: "This run has ended, so the agent no longer reads direction. Start a new run to continue.",
} as const;

export function Composer({
  runId,
  projectId,
  token,
  role,
  state,
  onChanged,
}: {
  runId: string | null;
  projectId: string | null;
  token: string;
  role?: string | null;
  /** The run's state; null when there is no run yet. */
  state: string | null;
  onChanged?: () => void;
}) {
  const router = useRouter();
  const [draft, setDraft] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [note, setNote] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [sent, setSent] = React.useState<SentDirection[]>([]);
  const [modelOpen, setModelOpen] = React.useState(false);

  const mode = composerMode(state);
  const disabledReason = mode === "plan" || mode === "finished" ? DISABLED_REASON[mode] : null;
  const action = parseDraft(draft, state);

  async function submit() {
    if (busy || disabledReason || action.kind === "empty") return;
    if (action.kind === "error") {
      setError(action.message);
      return;
    }
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      if (action.kind === "command" && runId) {
        if (action.name === "stop") {
          setNote(stopNotice((await stopAgent(runId, token)).status));
        } else {
          const r = await pauseOrResumeRun(runId, token, action.name);
          setNote(action.name === "resume"
            ? "Resumed: the agent continues from where it stopped."
            : r.status === "paused"
              ? "Paused: the agent cannot change files until you resume."
              : "Pausing: the agent may still finish the step it is on.");
        }
      } else if (action.kind === "text" && !runId) {
        const proposed = await proposePlan(action.text, token, projectId ?? getProjectId());
        if (proposed.projectId) setProjectId(proposed.projectId);
        setRunId(proposed.runId);
        router.push(`/projects/${encodeURIComponent(proposed.projectId ?? "none")}/runs/${encodeURIComponent(proposed.runId)}`);
        return; // keep the composer busy while the run page loads
      } else if (action.kind === "text" && runId) {
        await joinRun(runId, token).catch(() => {}); // steering needs a seat; already seated is fine
        const r = await sendRedirect(runId, token, { kind: "guidance", content: action.text, requestId: crypto.randomUUID() });
        const status = r.queued
          ? "Queued: the person in control must accept it before the agent sees it."
          : takesEffectText(r.takesEffect) ?? "Sent to the agent.";
        setSent((prev) => [...prev, { id: r.instructionId ?? String(r.seq ?? prev.length), text: action.text, status, seq: r.queued ? null : r.seq }]);
      }
      setDraft("");
      onChanged?.();
    } catch (e) {
      setError(loadErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  // The engine records how far it has handed direction to the agent; poll that while
  // something sent from this tab is still waiting.
  const waiting = sent.some((s) => s.seq !== null && !s.delivered);
  React.useEffect(() => {
    if (!waiting || !runId || !token) return;
    const timer = window.setInterval(() => {
      getHistory(runId, token, Number.MAX_SAFE_INTEGER)
        .then((page) => {
          const upTo = page.deliveredUpTo ?? 0;
          setSent((prev) => prev.map((s) => (s.seq !== null && s.seq <= upTo ? { ...s, delivered: true } : s)));
        })
        .catch(() => {}); // the send-time answer stays on screen
    }, 5000);
    return () => window.clearInterval(timer);
  }, [waiting, runId, token]);

  return (
    <div className="ws-composer" data-testid="composer">
      {sent.length > 0 && (
        <ul className="ws-sent" aria-label="Direction you sent">
          {sent.map((s) => (
            <li key={s.id}>
              <span className="ws-sent-text">{s.text}</span>
              <span className="ws-sent-status">{s.delivered ? "Delivered to the agent." : s.status}</span>
            </li>
          ))}
        </ul>
      )}
      <textarea
        id={COMPOSER_INPUT_ID}
        className="ws-composer-input"
        rows={2}
        value={draft}
        disabled={busy || disabledReason !== null}
        aria-label={mode === "goal" ? "Goal for a new run" : "Direction for the agent"}
        aria-describedby="ws-composer-help"
        placeholder={disabledReason ?? PLACEHOLDER[mode]}
        onChange={(e) => {
          setDraft(e.target.value);
          setError(null);
        }}
        onKeyDown={(e) => {
          // Enter sends; Shift+Enter is a new line. IME composition is left alone.
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            void submit();
          }
        }}
      />
      {draft.trimStart().startsWith("/") && mode === "direction" && (
        <ul className="ws-commands" aria-label="Commands">
          {COMMANDS.map((c) => (
            <li key={c.name} className={state !== null && c.ok(state) ? "" : "off"}>
              <code>/{c.name}</code> {c.hint}
              {state !== null && !c.ok(state) ? ` (${c.why})` : ""}
            </li>
          ))}
        </ul>
      )}
      {projectId && !disabledReason && (
        <RefAutocomplete draft={draft} projectId={projectId} token={token} onSelect={setDraft} />
      )}
      <div className="ws-composer-foot">
        <details className="ws-composer-model" onToggle={(e) => e.currentTarget.open && setModelOpen(true)}>
          <summary>Model and effort</summary>
          {/* Loaded on first open: a closed picker makes no model-service calls. */}
          {modelOpen && <ModelPrefsForm token={token} role={role} variant="compact" />}
        </details>
        <span id="ws-composer-help" className="ws-composer-help">
          {disabledReason ?? "Enter to send · Shift+Enter for a new line"}
        </span>
        <Button variant="primary" size="sm" disabled={busy || disabledReason !== null || action.kind === "empty"}
          onClick={() => void submit()} testId="composer-send">
          {busy ? "Sending…" : mode === "goal" ? "Propose plan" : "Send"}
        </Button>
      </div>
      {note && <p role="status" className="ws-composer-note">{note}</p>}
      {error && <p role="alert" className="ws-composer-error">{error}</p>}
    </div>
  );
}

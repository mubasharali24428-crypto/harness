"use client";

import { useEffect, useRef, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too — same as planGate.tsx.
import * as React from "react";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  approveBrief,
  createMeeting,
  generateBrief,
  getBrief,
  getMessages,
  postMessage,
  type BriefDto,
  type MeetingMsg,
} from "@/lib/api";
import { getUserId, getProjectId, getToken, loadSession, setGoal } from "@/lib/auth";
// M12-E1 (H-002): one send = one call. S6 answers "@ai" server-side (real
// model via the team's BYO key, honest fallback when none), so the client's
// second invokeAi POST is gone; sendWithAi owns the single-call flow + thinking.
import { sendWithAi } from "@/lib/meetingSend";
// M12-E6: the meeting pointer survives refresh. Persisted per team under
// `harness.meeting.<teamId>`; restored on mount and rehydrated through the
// existing GET messages/brief endpoints — no new backend surface.
import {
  clearMeeting,
  RESTORED_NOTE_LEAD,
  runMeetingRestore,
  saveMeeting,
} from "@/lib/session";

/** What the restore-time GET discovered about the saved meeting. */
type RestoreOutcome = "ok" | "missing" | "unreachable";

// M12-E6: restore orchestration for the mount effect lives in src/lib/session
// (runMeetingRestore) so the page file only default-exports the component —
// Next's generated page type check rejects extra exports from page files.
export default function Meeting() {
  const [meetingId, setMeetingId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MeetingMsg[]>([]);
  const [draft, setDraft] = useState("");
  const [brief, setBrief] = useState<BriefDto | null>(null);
  const [live, setLive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  // M12-E6: true once a persisted meeting was successfully brought back on mount.
  const [restoredFromStorage, setRestoredFromStorage] = useState(false);
  // M12-E1: true while an @ai send awaits S6's server-side reply (key decrypt
  // + model call can take seconds) — renders the thinking indicator.
  const [thinking, setThinking] = useState(false);

  // Generalized so reload can report how the GET went (restore needs to know
  // missing vs unreachable); every existing caller still ignores the result.
  const guard = async function guardFn<T>(fn: () => Promise<T>): Promise<T | undefined> {
    const token = getToken();
    if (!token) {
      setError("Not signed in — go to /login first.");
      return undefined;
    }
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (err) {
      setError(`${err instanceof Error ? err.message : String(err)} (is S6 up on :8006?)`);
      return undefined;
    } finally {
      setBusy(false);
    }
  };

  // C-006: no fabricated project fallback. Meetings are user actions — the
  // button is disabled until a real project has been bootstrapped post-login.
  const projectId = () => getProjectId();
  const hasProject = () => Boolean(projectId());

  // M12-E6: every time a meeting becomes current, remember it per team so a
  // refresh lands back in the same room instead of an idle empty one.
  const teamId = () => loadSession()?.principal?.teamId ?? null;
  const persist = (id: string, pid: string) => {
    const tid = teamId();
    if (tid) saveMeeting(tid, id, pid);
  };

  const reload = (id: string) =>
    guard(async (): Promise<RestoreOutcome> => {
      try {
        const msgs = await getMessages(id, getToken()!);
        if (msgs.length) setLive(true);
        setMessages(msgs);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return "missing";
        return "unreachable"; // S6 down/network blip — keep whatever we have
      }
      try {
        const b = await getBrief(id, getToken()!);
        setBrief(b);
      } catch {
        /* no brief yet — 404 expected pre-generation */
      }
      return "ok";
    });

  // M12-E6: restore the latest persisted meeting for this team exactly once on
  // mount. All orchestration + state disposition lives in runMeetingRestore
  // (src/lib/session.ts, unit-tested); this effect is its once-per-mount caller.
  const restoreAttemptedRef = useRef(false);
  useEffect(() => {
    if (restoreAttemptedRef.current) return;
    restoreAttemptedRef.current = true;
    runMeetingRestore({
      teamId: loadSession()?.principal?.teamId ?? null,
      token: getToken(),
      getProjectId: projectId,
      getMessages,
      getBrief,
      setMeetingId,
      setMessages,
      setLive,
      setBrief,
      setRestoredFromStorage,
      setError,
    });
  }, []);

  const startMeeting = () =>
    guard(async () => {
      const pid = projectId();
      if (!pid) {
        setError("No project selected — sign in again so your real project can be loaded.");
        return;
      }
      const m = await createMeeting(pid, "UI meeting", getToken()!);
      setMeetingId(m.id);
      setRestoredFromStorage(false);
      persist(m.id, pid); // latest wins — replaces any previously saved meeting
      await reload(m.id);
    });

  const send = (withAi: boolean) =>
    guard(async () => {
      let id = meetingId;
      let createdHere = false;
      if (!id) {
        const pid = projectId();
        if (!pid) {
          setError("No project selected — sign in again so your real project can be loaded.");
          return;
        }
        const m = await createMeeting(pid, "UI meeting", getToken()!);
        id = m.id;
        createdHere = true;
        setMeetingId(id);
      }
      const content = draft.trim() || "Please review the plan.";
      const uid = getUserId() ?? "ui-user";
      if (withAi) {
        // M12-E1: single invocation — S6 replies server-side to the "@ai"
        // message; thinking indicator covers the model-call latency.
        await sendWithAi({
          meetingId: id,
          userId: uid,
          content,
          token: getToken()!,
          onMessages: setMessages,
          onThinking: setThinking,
        });
      } else {
        await postMessage(id, uid, content, getToken()!);
      }
      setDraft("");
      if (createdHere && id) {
        const pid = projectId();
        if (pid) persist(id, pid);
      }
      if (!withAi) {
        await getMessages(id, getToken()!)
          .then(setMessages)
          .catch(() => {});
      }
      setLive(true);
    });

  const genBrief = () =>
    guard(async () => {
      if (!meetingId) return;
      const b = await generateBrief(meetingId, getToken()!);
      setBrief(b);
      setNote("brief generated — REVIEW then approve");
    });

  const doApproveBrief = () =>
    guard(async () => {
      if (!meetingId || !brief) return;
      await approveBrief(meetingId, brief.id, getUserId() ?? "ui-user", getToken()!);
      setBrief({ ...brief, status: "approved" });
      setGoal(brief.requirements?.join("; ") || "Implement the approved brief.");
      setNote("brief APPROVED — head to the Working Area to propose the plan");
    });

  return (
    <AppShell active="/meeting">
      <h1>
        Meeting Area{" "}
        <span className={`pill ${live ? "green" : "yellow"}`}>
          {restoredFromStorage ? "restored" : live ? "live" : "idle"}
          {meetingId ? ` · ${meetingId.slice(0, 8)}…` : ""}
        </span>
      </h1>
      {restoredFromStorage && (
        <p style={{ color: "var(--green)" }} data-testid="meeting-restored-note">
          {RESTORED_NOTE_LEAD} — picked up where you left off (transcript reloaded).
        </p>
      )}
      {!meetingId && (
        <p>
          <button className="btn primary" disabled={busy || !hasProject()} onClick={startMeeting}>
            Start a live meeting (creates in S6)
          </button>{" "}
          — or just send a message and one is created for you.
        </p>
      )}
      {meetingId && (
        <p>
          {/* M12-E6 explicit override: a persisted meeting never traps the user —
              clicking this deliberately starts fresh and replaces the pointer. */}
          <button className="btn" disabled={busy || !hasProject()} onClick={startMeeting}>
            Start a new meeting (replaces the current one)
          </button>
        </p>
      )}
      <div className="grid">
        <div className="card chat">
          <h2>Discussion (AI muted until you @ai)</h2>
          {messages.length === 0 && (
            <div className="msg human">
              <div className="who">you</div>Nothing yet — say something.
            </div>
          )}
          {messages.map((m, i) => {
            const isAi = Boolean(m.isAiGenerated) || m.userId === "ai-participant";
            return (
              <div className={`msg ${isAi ? "agent" : "human"}`} key={i}>
                <div className="who">{isAi ? "ai-participant" : m.userId ?? "user"}</div>
                {m.content}
              </div>
            );
          })}
          {/* M12-E1: shown while S6 works on an @ai reply (key + model call). */}
          {thinking && (
            <div className="msg agent" data-testid="ai-thinking">
              <div className="who">ai-participant</div>thinking…
            </div>
          )}
          <div className="chat-input">
            {/* M15-S3 (W4-VT2 carryover): placeholder-only naming fails WCAG
                4.1.2/3.3.2 — aria-label carries the accessible name; a visible
                label doesn't fit the compact chat composer design. */}
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Message team… use @ai to summon the agent"
              aria-label="Chat message"
            />
            <button className="btn" disabled={busy} onClick={() => void send(false)}>
              Send
            </button>
            <button
              className="btn primary"
              disabled={busy || thinking}
              onClick={() => void send(true)}
            >
              {thinking ? "@ai thinking…" : "Send @ai"}
            </button>
          </div>
        </div>

        <div className="card">
          <h2>Brief lifecycle</h2>
          <button className="btn" disabled={busy || !meetingId} onClick={genBrief}>
            Generate brief from transcript
          </button>{" "}
          {brief && (
            <button
              className="btn primary"
              disabled={busy || brief.status === "approved"}
              style={{ marginLeft: 8 }}
              onClick={doApproveBrief}
            >
              Approve brief
            </button>
          )}
          {note && <p>{note}</p>}
          {error && (
            <p style={{ color: "var(--red)" }}>{error}</p>
          )}
          {brief ? (
            <>
              <p>
                <strong>Status:</strong>{" "}
                <span className={`pill ${brief.status === "approved" ? "green" : "yellow"}`}>
                  {brief.status}
                </span>
              </p>
              <ul>{brief.requirements?.map((r) => <li key={r}>{r}</li>)}</ul>
            </>
          ) : (
            <p>No brief yet. Discuss, then generate.</p>
          )}
        </div>
      </div>
    </AppShell>
  );
}

"use client";

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import {
  approveBrief,
  createMeeting,
  generateBrief,
  getBrief,
  getMessages,
  invokeAi,
  postMessage,
  type BriefDto,
  type MeetingMsg,
} from "@/lib/api";
import { getUserId, getProjectId, getToken, setGoal } from "@/lib/auth";

export default function Meeting() {
  const [meetingId, setMeetingId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MeetingMsg[]>([]);
  const [draft, setDraft] = useState("");
  const [brief, setBrief] = useState<BriefDto | null>(null);
  const [live, setLive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const guard = async (fn: () => Promise<void>) => {
    const token = getToken();
    if (!token) {
      setError("Not signed in — go to /login first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(`${err instanceof Error ? err.message : String(err)} (is S6 up on :8006?)`);
    } finally {
      setBusy(false);
    }
  };

  // C-006: no fabricated project fallback. Meetings are user actions — the
  // button is disabled until a real project has been bootstrapped post-login.
  const projectId = () => getProjectId();
  const hasProject = () => Boolean(projectId());

  const reload = (id: string) =>
    guard(async () => {
      try {
        const msgs = await getMessages(id, getToken()!);
        if (msgs.length) setLive(true);
        setMessages(msgs);
      } catch {
        /* keep whatever we have */
      }
      try {
        const b = await getBrief(id, getToken()!);
        setBrief(b);
      } catch {
        /* no brief yet — 404 expected pre-generation */
      }
    });

  useEffect(() => {
    // No auto-create: an explicit user action mints the S6 resource.
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
      await reload(m.id);
    });

  const send = (withAi: boolean) =>
    guard(async () => {
      let id = meetingId;
      if (!id) {
        const pid = projectId();
        if (!pid) {
          setError("No project selected — sign in again so your real project can be loaded.");
          return;
        }
        const m = await createMeeting(pid, "UI meeting", getToken()!);
        id = m.id;
        setMeetingId(id);
      }
      const content = draft.trim() || "Please review the plan.";
      const uid = getUserId() ?? "ui-user";
      await postMessage(id, uid, withAi ? `@ai ${content}` : content, getToken()!);
      if (withAi) await invokeAi(id, uid, content, getToken()!);
      setDraft("");
      await getMessages(id, getToken()!)
        .then(setMessages)
        .catch(() => {});
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
          {live ? "live" : "idle"}
          {meetingId ? ` · ${meetingId.slice(0, 8)}…` : ""}
        </span>
      </h1>
      {!meetingId && (
        <p>
          <button className="btn primary" disabled={busy || !hasProject()} onClick={startMeeting}>
            Start a live meeting (creates in S6)
          </button>{" "}
          — or just send a message and one is created for you.
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
          <div className="chat-input">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Message team… use @ai to summon the agent"
            />
            <button className="btn" disabled={busy} onClick={() => void send(false)}>
              Send
            </button>
            <button className="btn primary" disabled={busy} onClick={() => void send(true)}>
              Send @ai
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

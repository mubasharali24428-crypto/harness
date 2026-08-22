"use client";

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { meeting, type BriefDto, type MeetingMsg } from "@/lib/api";

export default function Meeting() {
  const [meetingId, setMeetingId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MeetingMsg[]>([]);
  const [draft, setDraft] = useState("");
  const [brief, setBrief] = useState<BriefDto | null>(null);
  const [source, setSource] = useState<"live" | "mock">("mock");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const userId = () => localStorage.getItem("harness.userId") ?? "ui-user";
  const projectId = () => localStorage.getItem("harness.projectId") ?? "proj-test-1";

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setNote(`ERROR: ${err instanceof Error ? err.message : String(err)} (is S6 up on :8006?)`);
    } finally {
      setBusy(false);
    }
  };

  const createLiveMeeting = () =>
    guard(async () => {
      const m = await meeting.create(projectId(), "UI meeting");
      setMeetingId(m.id);
      setNote("");
      await reload(m.id);
    });

  const reload = async (id: string) =>
    guard(async () => {
      try {
        const msgs = await meeting.messages(id);
        if (msgs.length) setSource("live");
        setMessages(msgs);
      } catch {
        /* keep whatever we have */
      }
      try {
        const b = await meeting.getBrief(id);
        setBrief(b);
      } catch {
        /* no brief yet */
      }
    });

  const send = (invokeAi: boolean) =>
    guard(async () => {
      let id = meetingId;
      if (!id) {
        const m = await meeting.create(projectId(), "UI meeting");
        id = m.id;
        setMeetingId(id);
      }
      const content = draft.trim() || "Please review the plan.";
      await meeting.postMessage(id, userId(), invokeAi ? `@ai ${content}` : content);
      if (invokeAi) await meeting.invokeAi(id, userId(), content);
      setDraft("");
      await meeting.messages(id).then(setMessages).catch(() => {});
      setSource("live");
    });

  const genBrief = () =>
    guard(async () => {
      if (!meetingId) return;
      const b = await meeting.generateBrief(meetingId);
      setBrief(b);
      setNote("brief generated — REVIEW then approve");
    });

  const approveBrief = () =>
    guard(async () => {
      if (!meetingId || !brief) return;
      await meeting.approveBrief(meetingId, brief.id, userId());
      setBrief({ ...brief, status: "approved" });
      localStorage.setItem("harness.goal", brief.requirements?.join("; ") || "Implement the approved brief.");
      setNote("brief APPROVED — head to Working Area to propose the plan");
    });

  return (
    <AppShell active="/meeting">
      <h1>
        Meeting Area{" "}
        <span className={`pill ${source === "live" ? "green" : "yellow"}`}>
          {source}{meetingId ? ` · ${meetingId.slice(0, 8)}…` : ""}
        </span>
      </h1>
      {!meetingId && (
        <p>
          <button className="btn primary" disabled={busy} onClick={createLiveMeeting}>
            Start a live meeting (creates in S6)
          </button>{" "}
          — or just send a message and one is created for you.
        </p>
      )}
      <div className="grid">
        <div className="card chat">
          <h2>Discussion (AI muted until you @ai)</h2>
          {messages.length === 0 && (
            <>
              <div className="msg human">
                <div className="who">you</div>Nothing yet — say something.
              </div>
            </>
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
            <button className="btn" disabled={busy} onClick={() => send(false)}>
              Send
            </button>
            <button className="btn primary" disabled={busy} onClick={() => send(true)}>
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
              onClick={approveBrief}
            >
              Approve brief
            </button>
          )}
          {note && <p>{note}</p>}
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

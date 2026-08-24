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
  getMessagePage,
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
// M16-X4 (raw-17): markup migrated onto the shared DS primitives — Card /
// Button / Pill replace the class-only shells and the tinted note line moved
// onto the proposed Notice primitive. Visual parity intent; all testids,
// roles, aria-labels and the .pill/.card class hooks the e2e journey keys on
// are preserved verbatim.
import { Button, Card, Pill } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";

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
  // M16-X2: pagination — true while the server still holds older messages
  // beyond the loaded window; load-more appends them without touching newer
  // state (composer, thinking) at all.
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);

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

  // M16-X2: load-more — fetch the page strictly older than the oldest loaded
  // message and PREPEND it. Pure append of history; never touches newer
  // messages, the composer draft, or the thinking indicator. Cursor prefers
  // the stable messageSeq; legacy message ids are the fallback.
  const loadMore = () => {
    const token = getToken();
    if (!token || !meetingId || loadingOlder || messages.length === 0) return;
    const oldest = messages[0];
    setLoadingOlder(true);
    void getMessagePage(meetingId, token, {
      before: oldest.messageSeq ?? oldest.id,
    })
      .then((page) => {
        setMessages((prev) => [...page.items, ...prev]);
        setHasMore(page.hasMore);
      })
      .catch(() => {
        setError("Couldn't load older messages (is S6 up on :8006?)");
      })
      .finally(() => setLoadingOlder(false));
  };

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
        const page = await getMessagePage(id, getToken()!);
        if (page.items.length) setLive(true);
        setMessages(page.items);
        // M16-X2: remember whether older history remains server-side.
        setHasMore(page.hasMore);
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
      // M16-X2: restore reads through the paginated endpoint; only the
      // newest window hydrates on mount — older pages load via load-more.
      getMessages: async (id, tok) => (await getMessagePage(id, tok)).items,
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
        // M16-X2: refresh through the paginated read; a fresh page REPLACES
        // state and re-derives hasMore from the server envelope.
        await getMessagePage(id, getToken()!)
          .then((page) => {
            setMessages(page.items);
            setHasMore(page.hasMore);
          })
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
      {/* M16-X4: Pill keeps className="pill …" so the e2e journey's .pill.green
          hooks stay intact — parity face over the DS tone map. */}
      <h1>
        Meeting Area{" "}
        <Pill tone={live ? "green" : "yellow"} className={`pill ${live ? "green" : "yellow"}`}>
          {restoredFromStorage ? "restored" : live ? "live" : "idle"}
          {meetingId ? ` · ${meetingId.slice(0, 8)}…` : ""}
        </Pill>
      </h1>
      {restoredFromStorage && (
        // M16-X4: Notice(success) carries the tinted note line; the literal
        // data-testid + role=status are preserved for meetingRestore's pins.
        <Notice tone="success" role="status" data-testid="meeting-restored-note">
          {RESTORED_NOTE_LEAD} — picked up where you left off (transcript reloaded).
        </Notice>
      )}
      {!meetingId && (
        <p>
          <Button type="button" variant="primary" disabled={busy || !hasProject()} onClick={startMeeting}>
            Start a live meeting (creates in S6)
          </Button>{" "}
          — or just send a message and one is created for you.
        </p>
      )}
      {meetingId && (
        <p>
          {/* M12-E6 explicit override: a persisted meeting never traps the user —
              clicking this deliberately starts fresh and replaces the pointer. */}
          <Button type="button" disabled={busy || !hasProject()} onClick={startMeeting}>
            Start a new meeting (replaces the current one)
          </Button>
        </p>
      )}
      <div className="grid">
        {/* M16-X4: Card(as="div") wraps the chat column — .chat flex layout
            kept via className, title renders the legacy .card h2 face. */}
        <Card as="div" className="card chat" title="Discussion (AI muted until you @ai)">
          {messages.length === 0 && (
            <div className="msg human">
              <div className="who">you</div>Nothing yet — say something.
            </div>
          )}
          {/* M16-X2: load-more — appears only when the server reports older
              history; prepends the older page without touching newer state. */}
          {hasMore && (
            <p>
              <Button
                type="button"
                data-testid="load-older"
                loading={loadingOlder}
                loadingText="Loading…"
                disabled={busy || loadingOlder}
                onClick={() => void loadMore()}
              >
                {loadingOlder ? "Loading…" : "Load earlier messages"}
              </Button>
            </p>
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
            <Button type="button" disabled={busy} onClick={() => void send(false)}>
              Send
            </Button>
            {/* M16-X4: loading swaps the label while S6 works (aria-busy). */}
            <Button
              type="button"
              variant="primary"
              disabled={busy || thinking}
              loading={thinking}
              loadingText="@ai thinking…"
              onClick={() => void send(true)}
            >
              {thinking ? "@ai thinking…" : "Send @ai"}
            </Button>
          </div>
        </Card>

        {/* M16-X4: brief-lifecycle card onto Card; status/error lines onto
            Pill/Notice with their e2e class hooks kept. */}
        <Card title="Brief lifecycle">
          <Button type="button" disabled={busy || !meetingId} onClick={genBrief}>
            Generate brief from transcript
          </Button>{" "}
          {brief && (
            <Button
              type="button"
              variant="primary"
              disabled={busy || brief.status === "approved"}
              style={{ marginLeft: 8 }}
              onClick={doApproveBrief}
            >
              Approve brief
            </Button>
          )}
          {note && <p>{note}</p>}
          {error && <Notice tone="danger">{error}</Notice>}
          {brief ? (
            <>
              <p>
                <strong>Status:</strong>{" "}
                <Pill
                  tone={brief.status === "approved" ? "green" : "yellow"}
                  className={`pill ${brief.status === "approved" ? "green" : "yellow"}`}
                >
                  {brief.status}
                </Pill>
              </p>
              <ul>{brief.requirements?.map((r) => <li key={r}>{r}</li>)}</ul>
            </>
          ) : (
            <p>No brief yet. Discuss, then generate.</p>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

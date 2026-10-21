"use client";

// Multiplayer pilot S3 — shared run panel: who leads, who is here, what is
// blocked and who must decide, direction composer, handoff, and the ordered
// collaboration history. Presentational SharedRunView is props-driven (tested
// by static render); SharedRunPanel adds fetching. Part 46: events arrive through useCollabStream
// (socket + cursor + reducer + polling fallback, docs/realtime.md section 5); roster and approvals are
// re-read on every delivered batch and on a safety poll (3 s when the socket is not live, 20 s when it is).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as React from "react";
import { Button } from "@/components/ui";
import { Card } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { Pill } from "@/components/ui";
import { loadSession } from "@/lib/auth";
import { ApiError, getPlan } from "@/lib/api";
import { createRefetchCoalescer } from "@/lib/backoff";
import { parkFocus } from "@/lib/focus";
import { formatDateTime } from "@/lib/intl";
import { AnnounceContext, useAnnouncer } from "@/components/LiveAnnouncer";
import {
  type Approval, type CollabEvent, type RedirectKind, type Roster,
  hasUnappliedStaged, applyApproval, claimLeader, decideApproval, decideHandoff, getRoster, joinRun,
  listApprovals, requestHandoff, sendRedirect,
} from "./collabApi";
import {
  type CollabView, approvalAnnouncements, approvalLabel, clipDiff, deriveCollabView, describeDecision,
  fileStatuses, freshEventLines, isOpenApproval, mergeEvents, plainError, redirectProblem, reopenedApprovals,
} from "./collabModel";
import { type StreamView, useCollabStream } from "./useCollabStream";
import { ConnectionStatus } from "./connectionStatus";
import { HistoryLog } from "./historyLog";
import { cooldownSeconds, mutationError } from "./realtimeCore";
import { DiscussionSection } from "./discussionSection";
import { AgentTreeSection } from "./agentTree";
import { RunControlSection } from "./controlSection";
import { RunIntentSection } from "./intentSection";
import { RunOwnerSection } from "./ownerSection";
import { PresenceSection } from "./presenceSection";

const muted = { color: "var(--muted)" } as const;
const mono = { fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12 } as const;

export interface SharedRunViewProps {
  me: string;
  roster: Roster | null;
  view: CollabView;
  approvals: Approval[];
  events: CollabEvent[];
  error: string | null;
  busy: boolean;
  /** Approval ids whose proposal changed after approval (earlier approval no longer applies). */
  reopened?: string[];
  /** The run's goal as recorded on the server (null until known). */
  goal?: string | null;
  /** A polite note about the last thing you did (e.g. your direction was queued, not sent). */
  notice?: string | null;
  /** Part 46: live connection state; the indicator renders only when given. */
  connection?: StreamView;
  onReload?: () => void;
  /** Presence, control and agent sections (parts 41/42/45), rendered between participants and decisions. */
  children?: React.ReactNode;
  /** Part 43: the discussion panel, rendered after the team history so it never pushes decisions down. */
  discussion?: React.ReactNode;
  onJoin: () => void;
  onClaim: () => void;
  onDecide: (a: Approval, approve: boolean) => void;
  onApply: (a: Approval) => void;
  onHandoff: (toUserId: string) => void;
  onHandoffDecision: (id: string, accept: boolean) => void;
  onRedirect: (kind: RedirectKind, content: string, reason: string) => void;
}

export const PILOT_NOT_ENABLED = "PILOT_NOT_ENABLED";

export function NotEnabled() {
  return (
    <Card title="Shared run" testId="shared-run-not-enabled">
      <Notice tone="info" role="status">
        Shared runs are not enabled for your team yet. Your own run is unaffected.
      </Notice>
    </Card>
  );
}

// Colour is only a hint; every Pill also carries the status in words.
const stateTone = (state: string) =>
  ["approved", "executing", "completed"].includes(state) ? "green" as const
    : ["pending", "invalidated", "expired"].includes(state) ? "yellow" as const : "red" as const;

// An approved/rejected card loses its buttons; park focus on the card so keyboard users are not dropped to <body>.
const keepFocus = (e: React.SyntheticEvent<HTMLElement>) =>
  (e.currentTarget.closest("[data-approval-card]") as HTMLElement | null)?.focus();

export function SharedRunView(p: SharedRunViewProps) {
  const { view } = p;
  const ids = React.useId();
  const [kind, setKind] = useState<RedirectKind>("guidance");
  const [content, setContent] = useState("");
  const [reason, setReason] = useState("");
  const [target, setTarget] = useState("");
  const problem = redirectProblem(kind, content, reason);
  const leaderOnly = kind !== "guidance";
  const open = p.approvals.filter(isOpenApproval);
  const decided = p.approvals.filter((a) => a.state !== "pending");
  const files = fileStatuses(p.approvals, view.leader);
  const reopened = [...(p.reopened ?? []), ...p.approvals.filter((a) => a.amendedAfterApproval).map((a) => a.id)];
  const owner = view.leader ? `${view.leader}${view.isLeader ? " (you)" : ""}` : null;

  return (
    <Card title="Shared run" testId="shared-run-panel" aria-label="Shared run" tabIndex={-1} data-focus-park="">
      {p.connection && <ConnectionStatus status={p.connection.status} updateRequired={p.connection.updateRequired} onReload={p.onReload} />}
      {p.error && <Notice tone="danger" role="alert">{p.error}</Notice>}
      {p.notice && <Notice tone="info" role="status" testId="direction-notice">{p.notice}</Notice>}

      {p.goal && <p data-testid="goal-line" style={{ marginTop: 0 }}>Goal: <strong>{p.goal}</strong></p>}
      <p data-testid="leader-line" style={{ marginTop: 0 }}>
        {view.leader
          ? <>Led by <strong>{view.leader}</strong>{view.isLeader ? " (you)" : ""}. </>
          : <>This run has no session leader yet. </>}
        {view.myRole
          ? <>You are {view.myRole === "observer" ? "an observer" : `a ${view.myRole}`}.</>
          : <>You have not joined this run.</>}
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {!view.joined && <Button variant="primary" size="sm" disabled={p.busy} onClick={(e) => { parkFocus(e); p.onJoin(); }}>Join this run</Button>}
        {view.canClaimLeader && <Button size="sm" disabled={p.busy} onClick={(e) => { parkFocus(e); p.onClaim(); }}>Become session leader</Button>}
      </div>

      {view.blockedReason && (
        <Notice tone="warning" role="status" testId="blocked-banner">{view.blockedReason}</Notice>
      )}
      {view.handoffForMe && (
        <Notice tone="info" role="status" testId="handoff-offer">
          {view.handoffForMe.from} offered you the lead.{" "}
          <Button size="sm" variant="primary" disabled={p.busy} onClick={(e) => { parkFocus(e); p.onHandoffDecision(view.handoffForMe!.id, true); }}>Accept</Button>{" "}
          <Button size="sm" disabled={p.busy} onClick={(e) => { parkFocus(e); p.onHandoffDecision(view.handoffForMe!.id, false); }}>Decline</Button>
        </Notice>
      )}
      {view.handoffFromMe && (
        <Notice tone="info" role="status">Waiting for {view.handoffFromMe.to} to accept the lead. You stay in charge until they do.</Notice>
      )}

      <h3 style={{ fontSize: 13 }}>Participants</h3>
      <ul aria-label="Participants" style={{ margin: 0, paddingLeft: 18 }}>
        {(p.roster?.participants ?? []).map((m) => (
          <li key={m.userId}>{m.userId}{m.userId === p.me ? " (you)" : ""} <Pill tone={m.role === "leader" ? "green" : "blue"}>{m.role}</Pill></li>
        ))}
        {(p.roster?.participants.length ?? 0) === 0 && <li style={muted}>No one has joined yet.</li>}
      </ul>
      {view.handoffTargets.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <label>Hand the lead to{" "}
            <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Hand off lead to">
              <option value="">Choose a teammate…</option>
              {view.handoffTargets.map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
          </label>{" "}
          <Button size="sm" disabled={p.busy || !target} onClick={(e) => { parkFocus(e); p.onHandoff(target); }}>Offer handoff</Button>
        </div>
      )}

      {p.children}

      <section aria-label="Pending decisions">
        <h3 style={{ fontSize: 13 }}>Pending decisions ({open.filter((a) => a.state === "pending").length})</h3>
        {open.length === 0 && <p style={muted}>No high-impact requests.</p>}
        {open.map((a) => (
          <div key={a.id} data-testid={`approval-${a.id}`} data-approval-card tabIndex={-1} role="group"
            aria-label={`${a.action === "delete" ? "Delete" : "Change"} ${a.paths.join(", ")}`}
            style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, marginBottom: 8 }}>
            <div>
              <strong>{a.action === "delete" ? "Delete" : "Substantially modify"}</strong>{" "}
              <Pill tone={stateTone(a.state)}>
                {approvalLabel(a, view.leader)}
              </Pill>
            </div>
            <p style={{ margin: "4px 0" }}>{a.summary}</p>
            {reopened.includes(a.id) && a.state === "pending" && (
              <Notice tone="warning" role="status" testId={`reopened-${a.id}`}>
                This proposal changed after it was approved. The earlier approval no longer applies; it needs a new decision.
              </Notice>
            )}
            {a.state === "pending" && (
              <p data-testid={`responsible-${a.id}`} id={`${ids}-${a.id}`} style={{ margin: "4px 0" }}>
                {owner
                  ? <>Decision needed from <strong>{owner}</strong>.</>
                  : <>Nobody can decide this yet: the run has no session leader.</>}
              </p>
            )}
            <p style={{ ...muted, margin: "4px 0" }}>Requested by {a.requester}{a.decidedBy ? `, decided by ${a.decidedBy}` : ""}{a.decidedAt && <> on <time dateTime={a.decidedAt} data-testid={`decided-${a.id}`}>{formatDateTime(a.decidedAt)}</time></>}. Files: <span style={mono}>{a.paths.join(", ")}</span>. Proposal version <span style={mono} data-testid={`digest-${a.id}`}>{a.digest.slice(0, 8)}</span>.</p>
            {a.diff ? <pre aria-label="Proposed change" tabIndex={0} style={{ ...mono, maxHeight: 160, overflow: "auto", background: "var(--panel-2)", padding: 8 }}>{clipDiff(a.diff)}</pre> : null}
            {a.requester === "system:staging" && (
              <p style={muted}>This change is withheld. Approval and application are separate steps.</p>
            )}
            {a.requester === "system:staging" && a.state === "approved" && (view.isLeader
              ? <Button size="sm" variant="primary" disabled={p.busy} onClick={(e) => { keepFocus(e); p.onApply(a); }}>Apply approved change</Button>
              : <p style={muted}>Waiting for {view.leader ?? "the session leader"} to apply this change.</p>)}
            {a.state === "pending" && (view.isLeader
              ? <div role="group" aria-label="Decide" style={{ display: "flex", gap: 8 }}>
                  <Button size="sm" variant="primary" disabled={p.busy} aria-describedby={`${ids}-${a.id}`} onClick={(e) => { keepFocus(e); p.onDecide(a, true); }}>Approve this change</Button>
                  <Button size="sm" variant="danger" disabled={p.busy} aria-describedby={`${ids}-${a.id}`} onClick={(e) => { keepFocus(e); p.onDecide(a, false); }}>Reject</Button>
                </div>
              : <p style={muted}>Only {view.leader ?? "a session leader"} can decide this.</p>)}
          </div>
        ))}
      </section>

      <section aria-label="Changed files">
        <h3 style={{ fontSize: 13 }}>Changed files</h3>
        {files.length === 0
          ? <p style={muted}>No changed files need a decision.</p>
          : <ul style={{ margin: 0, paddingLeft: 18 }}>
              {files.map((f) => (
                <li key={f.path} data-testid={`file-${f.path}`}><span style={mono}>{f.path}</span>{" "}
                  <Pill tone={stateTone(f.state)}>{f.label}</Pill></li>
              ))}
            </ul>}
      </section>

      <section aria-label="Decision history">
        <h3 style={{ fontSize: 13 }}>Decision history</h3>
        <ol style={{ margin: 0, paddingLeft: 18 }}>
          {decided.map((a) => (
            <li key={a.id} data-testid={`decision-${a.id}`}>{describeDecision(a)} <Pill tone={stateTone(a.state)}>{approvalLabel(a, view.leader)}</Pill></li>
          ))}
          {decided.length === 0 && <li style={muted}>No decisions yet.</li>}
        </ol>
      </section>

      <h3 style={{ fontSize: 13 }}>Give direction</h3>
      {!view.canRedirect
        ? <p style={muted}>{view.joined ? "Observers can follow along but not steer." : "Join the run to steer it."}</p>
        : <form onSubmit={(e) => { e.preventDefault(); if (!problem) { p.onRedirect(kind, content, reason); setContent(""); setReason(""); } }}>
            <label>Type{" "}
              <select value={kind} onChange={(e) => setKind(e.target.value as RedirectKind)} aria-label="Direction type">
                <option value="guidance">Guidance (anyone)</option>
                <option value="scope_change" disabled={!view.isLeader}>Change scope (leader)</option>
                <option value="permission_change" disabled={!view.isLeader}>Change permissions (leader)</option>
              </select>
            </label>
            <textarea aria-label="Direction for the agent" onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !problem && !p.busy) { e.preventDefault(); p.onRedirect(kind, content, reason); setContent(""); setReason(""); } }} aria-invalid={(problem !== null && content.length > 0) || undefined} aria-describedby={problem && content.length > 0 ? `${ids}-problem` : undefined} rows={3} value={content} onChange={(e) => setContent(e.target.value)} style={{ display: "block", width: "100%", margin: "6px 0" }} />
            {leaderOnly && <input aria-label="Reason for this change" aria-required="true" placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} style={{ display: "block", width: "100%", marginBottom: 6 }} />}
            {leaderOnly && <p style={muted}>Changing scope or permissions voids open approvals; they must be requested again.</p>}
            <p style={muted}>The agent picks this up the next time it starts or resumes, not mid-step. Press Ctrl+Enter (Cmd+Enter on a Mac) in the box to send.</p>
            <Button type="submit" size="sm" variant="primary" disabled={p.busy || problem !== null}>Send direction</Button>
            {problem && content.length > 0 && <span role="status" id={`${ids}-problem`} style={{ ...muted, marginLeft: 8 }}>{problem}</span>}
          </form>}

      <h3 style={{ fontSize: 13 }}>Team history</h3>
      <HistoryLog events={p.events} />

      {p.discussion}
    </Card>
  );
}

const POLL_MS = 3000;
const POLL_LIVE_MS = 20000; // safety net while the push socket is live (docs/realtime.md 5.3)
const WAKE_MS = 250; // coalesce a burst of delivered events into one re-read of roster and approvals

export function SharedRunPanel({ runId, projectId, onStagedPendingChange }: {
  runId: string; projectId?: string; onStagedPendingChange?: (pending: boolean) => void;
}) {
  const session = loadSession();
  const token = session?.token ?? null;
  const me = session?.principal.userId ?? "";
  const canLead = session?.principal.role === "admin" || session?.principal.role === "engineer";
  const [roster, setRoster] = useState<Roster | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [events, setEvents] = useState<CollabEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cooling, setCooling] = useState(false);
  const [notEnabled, setNotEnabled] = useState(false);
  const [goal, setGoal] = useState<string | null>(null);
  const [reopened, setReopened] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const seen = useRef<Approval[] | null>(null);
  const meRef = useRef({ me, leader: null as string | null });
  const { announce, region } = useAnnouncer();

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const [r, a] = await Promise.all([getRoster(runId, token), listApprovals(runId, token)]);
      setRoster(r);
      setApprovals(a.approvals);
      meRef.current.leader = r.leader;
      const before = seen.current;
      if (before) {
        const again = reopenedApprovals(before, a.approvals);
        if (again.length) setReopened((prev) => [...new Set([...prev, ...again])]);
        // A new request is spoken once: assertively for the person who must decide, politely for the rest.
        for (const x of approvalAnnouncements(before, a.approvals, r.leader, meRef.current.me)) announce(x.text, x.urgent);
      }
      seen.current = a.approvals;
      onStagedPendingChange?.(hasUnappliedStaged(a.approvals));
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.code === PILOT_NOT_ENABLED) setNotEnabled(true);
      else setError(plainError(e) ?? (e instanceof ApiError ? e.message : String((e as Error)?.message ?? e)));
    }
  }, [runId, token, onStagedPendingChange, announce]);
  meRef.current.me = me;

  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const wake = useMemo(() => createRefetchCoalescer(WAKE_MS, () => void refreshRef.current()), []);
  useEffect(() => () => wake.cancel(), [wake]);

  // Part 46: ordered, de-duplicated events from the stream. Each delivered batch re-reads roster/approvals.
  const onEvents = useCallback((frames: CollabEvent[]) => {
    setEvents((prev) => mergeEvents(prev, frames));
    wake.push();
    for (const line of freshEventLines(frames, meRef.current.me, Date.now())) announce(line); // catching up on history is silent
  }, [wake, announce]);
  const onReset = useCallback(() => setEvents([]), []);
  const stream = useCollabStream({ projectId, runId, token, enabled: !!token && !notEnabled, onEvents, onReset, announce });

  useEffect(() => { // the waiting suggestion itself stays listed under "Suggestions waiting"
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 10000);
    return () => clearTimeout(t);
  }, [notice]);

  useEffect(() => {
    if (token) getPlan(runId, token).then((plan) => setGoal(plan?.goal?.trim() || null), () => setGoal(null));
  }, [runId, token]);

  const live = stream.status === "live";
  useEffect(() => {
    if (notEnabled) return;
    void refresh();
    const t = setInterval(() => void refresh(), live ? POLL_LIVE_MS : POLL_MS);
    return () => clearInterval(t);
  }, [refresh, notEnabled, live]);

  const act = (fn: () => Promise<unknown>) => async () => {
    if (!token) return;
    setBusy(true);
    try { await fn(); setError(null); } catch (e) {
      // Stale view: the refresh below re-reads, the message says so, nothing is resubmitted (5.4).
      setError(mutationError(e, (x) => plainError(x) ?? (x instanceof ApiError ? x.message : String((x as Error)?.message ?? x))));
      const wait = cooldownSeconds(e);
      if (wait !== null) { setCooling(true); setTimeout(() => setCooling(false), wait * 1000); }
    } finally { setBusy(false); await refresh(); }
  };
  const run = (fn: () => Promise<unknown>) => void act(fn)();

  if (!token) return null;
  if (notEnabled) return <NotEnabled />;
  const view = deriveCollabView(roster, approvals, me, canLead);
  const tick = events.length ? events[events.length - 1].seq : 0; // new collab event -> side sections refetch
  const changed = () => void refresh();
  const people = (roster?.participants ?? []).map((m) => m.userId);
  return (
    <AnnounceContext.Provider value={announce}>
      {region}
      <SharedRunView
        me={me} roster={roster} view={view} approvals={approvals} events={events}
        error={error} busy={busy || cooling} reopened={reopened} goal={goal} notice={notice}
        connection={stream} onReload={() => window.location.reload()}
        discussion={<DiscussionSection scope={{ kind: "run", id: runId }} token={token} me={me} role={session?.principal.role} canSteer={canLead && view.joined} people={people} tick={tick} />}
        onJoin={() => run(() => joinRun(runId, token))}
        onClaim={() => run(() => claimLeader(runId, token))}
        onApply={(a) => run(() => applyApproval(runId, token, a))}
        onDecide={(a, ok) => run(() => decideApproval(runId, token, a, ok))}
        onHandoff={(to) => run(() => requestHandoff(runId, token, to))}
        onHandoffDecision={(id, ok) => run(() => decideHandoff(runId, token, id, ok))}
        onRedirect={(kind, content, reason) =>
          run(async () => {
            const r = await sendRedirect(runId, token, { kind, content, reason: reason || undefined, requestId: crypto.randomUUID() });
            // Guidance from someone who does not hold control is queued, not sent: say so.
            setNotice(r.queued
              ? `Your direction is queued. ${view.leader ?? "The person in control"} must accept it before the agent sees it.`
              : null);
          })}
      >
        <PresenceSection runId={runId} token={token} tick={tick} onChanged={changed} />
        <RunControlSection runId={runId} token={token} tick={tick} isAdmin={session?.principal.role === "admin"} onChanged={changed} />
        <RunIntentSection runId={runId} token={token} tick={tick} canEdit={canLead} onChanged={changed} />
        <RunOwnerSection runId={runId} token={token} tick={tick} me={me} />
        <AgentTreeSection runId={runId} token={token} tick={tick} canSteer={canLead && view.joined} />
      </SharedRunView>
    </AnnounceContext.Provider>
  );
}

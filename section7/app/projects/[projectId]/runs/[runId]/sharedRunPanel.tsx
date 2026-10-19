"use client";

// Multiplayer pilot S3 — shared run panel: who leads, who is here, what is
// blocked and who must decide, direction composer, handoff, and the ordered
// collaboration history. Presentational SharedRunView is props-driven (tested
// by static render); SharedRunPanel adds fetching + 3s polling (no collab push
// channel exists yet, history resumes by seq).
import { useCallback, useEffect, useRef, useState } from "react";
import * as React from "react";
import { Button } from "@/components/ui";
import { Card } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { Pill } from "@/components/ui";
import { loadSession } from "@/lib/auth";
import { ApiError, getPlan } from "@/lib/api";
import {
  type Approval, type CollabEvent, type RedirectKind, type Roster,
  hasUnappliedStaged, applyApproval, claimLeader, decideApproval, decideHandoff, getHistory, getRoster, joinRun,
  listApprovals, requestHandoff, sendRedirect,
  openCollabSocket,
} from "./collabApi";
import {
  type CollabView, approvalLabel, clipDiff, deriveCollabView, describeDecision,
  describeEvent, fileStatuses, isOpenApproval, mergeEvents, redirectProblem, reopenedApprovals,
} from "./collabModel";

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
  const owner = view.leader ? `${view.leader}${view.isLeader ? " (you)" : ""}` : null;

  return (
    <Card title="Shared run" testId="shared-run-panel">
      {p.error && <Notice tone="danger" role="alert">{p.error}</Notice>}

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
        {!view.joined && <Button variant="primary" size="sm" disabled={p.busy} onClick={p.onJoin}>Join this run</Button>}
        {view.canClaimLeader && <Button size="sm" disabled={p.busy} onClick={p.onClaim}>Become session leader</Button>}
      </div>

      {view.blockedReason && (
        <Notice tone="warning" role="status" testId="blocked-banner">{view.blockedReason}</Notice>
      )}
      {view.handoffForMe && (
        <Notice tone="info" role="status" testId="handoff-offer">
          {view.handoffForMe.from} offered you the lead.{" "}
          <Button size="sm" variant="primary" disabled={p.busy} onClick={() => p.onHandoffDecision(view.handoffForMe!.id, true)}>Accept</Button>{" "}
          <Button size="sm" disabled={p.busy} onClick={() => p.onHandoffDecision(view.handoffForMe!.id, false)}>Decline</Button>
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
          <Button size="sm" disabled={p.busy || !target} onClick={() => p.onHandoff(target)}>Offer handoff</Button>
        </div>
      )}

      <section aria-label="Pending decisions" aria-live="polite">
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
            {p.reopened?.includes(a.id) && a.state === "pending" && (
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
            <p style={{ ...muted, margin: "4px 0" }}>Requested by {a.requester}{a.decidedBy ? `, decided by ${a.decidedBy}` : ""}. Files: <span style={mono}>{a.paths.join(", ")}</span>. Proposal version <span style={mono} data-testid={`digest-${a.id}`}>{a.digest.slice(0, 8)}</span>.</p>
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
            <textarea aria-label="Direction for the agent" aria-invalid={(problem !== null && content.length > 0) || undefined} aria-describedby={problem && content.length > 0 ? `${ids}-problem` : undefined} rows={3} value={content} onChange={(e) => setContent(e.target.value)} style={{ display: "block", width: "100%", margin: "6px 0" }} />
            {leaderOnly && <input aria-label="Reason for this change" aria-required="true" placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} style={{ display: "block", width: "100%", marginBottom: 6 }} />}
            {leaderOnly && <p style={muted}>Changing scope or permissions voids open approvals; they must be requested again.</p>}
            <p style={muted}>The agent picks this up the next time it starts or resumes, not mid-step.</p>
            <Button type="submit" size="sm" variant="primary" disabled={p.busy || problem !== null}>Send direction</Button>
            {problem && content.length > 0 && <span role="status" id={`${ids}-problem`} style={{ ...muted, marginLeft: 8 }}>{problem}</span>}
          </form>}

      <h3 style={{ fontSize: 13 }}>Team history</h3>
      <ol role="log" aria-label="Collaboration history" style={{ margin: 0, paddingLeft: 28, maxHeight: 220, overflow: "auto" }}>
        {p.events.map((e) => (
          <li key={e.seq}><span style={muted}>{new Date(e.ts).toLocaleTimeString()}</span> {describeEvent(e)}</li>
        ))}
        {p.events.length === 0 && <li style={muted}>Nothing yet.</li>}
      </ol>
    </Card>
  );
}

const POLL_MS = 3000;
const POLL_LIVE_MS = 20000; // safety net while the push socket is up

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
  const [notEnabled, setNotEnabled] = useState(false);
  const [goal, setGoal] = useState<string | null>(null);
  const [reopened, setReopened] = useState<string[]>([]);
  const [live, setLive] = useState(false);
  const lastSeq = useRef(0);
  const seen = useRef<Approval[]>([]);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const [r, a, h] = await Promise.all([
        getRoster(runId, token), listApprovals(runId, token), getHistory(runId, token, lastSeq.current),
      ]);
      setRoster(r);
      setApprovals(a.approvals);
      const again = reopenedApprovals(seen.current, a.approvals);
      seen.current = a.approvals;
      if (again.length) setReopened((prev) => [...new Set([...prev, ...again])]);
      onStagedPendingChange?.(hasUnappliedStaged(a.approvals));
      if (h.events.length) {
        lastSeq.current = h.events[h.events.length - 1].seq;
        setEvents((prev) => mergeEvents(prev, h.events));
      }
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.code === PILOT_NOT_ENABLED) setNotEnabled(true);
      else setError(e instanceof ApiError ? e.message : String((e as Error)?.message ?? e));
    }
  }, [runId, token, onStagedPendingChange]);

  useEffect(() => {
    if (token) getPlan(runId, token).then((plan) => setGoal(plan?.goal?.trim() || null), () => setGoal(null));
  }, [runId, token]);

  useEffect(() => {
    if (notEnabled) return;
    void refresh();
    const t = setInterval(() => void refresh(), live ? POLL_LIVE_MS : POLL_MS);
    return () => clearInterval(t);
  }, [refresh, notEnabled, live]);

  useEffect(() => {
    if (!token || !projectId || notEnabled || typeof WebSocket === "undefined") return;
    return openCollabSocket(projectId, runId, token, () => lastSeq.current, () => void refresh(), setLive);
  }, [projectId, runId, token, notEnabled, refresh]);

  const act = (fn: () => Promise<unknown>) => async () => {
    if (!token) return;
    setBusy(true);
    try { await fn(); setError(null); } catch (e) {
      setError(e instanceof ApiError ? e.message : String((e as Error)?.message ?? e));
    } finally { setBusy(false); await refresh(); }
  };
  const run = (fn: () => Promise<unknown>) => void act(fn)();

  if (!token) return null;
  if (notEnabled) return <NotEnabled />;
  const view = deriveCollabView(roster, approvals, me, canLead);
  return (
    <SharedRunView
      me={me} roster={roster} view={view} approvals={approvals} events={events}
      error={error} busy={busy} reopened={reopened} goal={goal}
      onJoin={() => run(() => joinRun(runId, token))}
      onClaim={() => run(() => claimLeader(runId, token))}
      onApply={(a) => run(() => applyApproval(runId, token, a))}
      onDecide={(a, ok) => run(() => decideApproval(runId, token, a, ok))}
      onHandoff={(to) => run(() => requestHandoff(runId, token, to))}
      onHandoffDecision={(id, ok) => run(() => decideHandoff(runId, token, id, ok))}
      onRedirect={(kind, content, reason) =>
        run(() => sendRedirect(runId, token, { kind, content, reason: reason || undefined, requestId: crypto.randomUUID() }))}
    />
  );
}

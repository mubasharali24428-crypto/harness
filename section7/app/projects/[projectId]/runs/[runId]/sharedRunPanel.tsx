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
import { ApiError } from "@/lib/api";
import {
  type Approval, type CollabEvent, type RedirectKind, type Roster,
  claimLeader, decideApproval, decideHandoff, getHistory, getRoster, joinRun,
  listApprovals, requestHandoff, sendRedirect,
} from "./collabApi";
import {
  APPROVAL_STATE_LABEL, type CollabView, clipDiff, deriveCollabView,
  describeEvent, redirectProblem,
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
  onJoin: () => void;
  onClaim: () => void;
  onDecide: (a: Approval, approve: boolean) => void;
  onHandoff: (toUserId: string) => void;
  onHandoffDecision: (id: string, accept: boolean) => void;
  onRedirect: (kind: RedirectKind, content: string, reason: string) => void;
}

export function SharedRunView(p: SharedRunViewProps) {
  const { view } = p;
  const [kind, setKind] = useState<RedirectKind>("guidance");
  const [content, setContent] = useState("");
  const [reason, setReason] = useState("");
  const [target, setTarget] = useState("");
  const problem = redirectProblem(kind, content, reason);
  const leaderOnly = kind !== "guidance";
  const shown = p.approvals;

  return (
    <Card title="Shared run" testId="shared-run-panel">
      {p.error && <Notice tone="danger" role="alert">{p.error}</Notice>}

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

      <h3 style={{ fontSize: 13 }}>Decisions</h3>
      {shown.length === 0 && <p style={muted}>No high-impact requests.</p>}
      {shown.map((a) => (
        <div key={a.id} data-testid={`approval-${a.id}`} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, marginBottom: 8 }}>
          <div>
            <strong>{a.action === "delete" ? "Delete" : "Substantially modify"}</strong>{" "}
            <Pill tone={a.state === "approved" || a.state === "completed" ? "green" : a.state === "pending" ? "yellow" : "red"}>
              {APPROVAL_STATE_LABEL[a.state] ?? a.state}
            </Pill>
          </div>
          <p style={{ margin: "4px 0" }}>{a.summary}</p>
          <p style={{ ...muted, margin: "4px 0" }}>Requested by {a.requester}{a.decidedBy ? `, decided by ${a.decidedBy}` : ""}. Files: <span style={mono}>{a.paths.join(", ")}</span></p>
          {a.diff ? <pre aria-label="Proposed change" style={{ ...mono, maxHeight: 160, overflow: "auto", background: "var(--panel-2)", padding: 8 }}>{clipDiff(a.diff)}</pre> : null}
          {a.state === "pending" && (view.isLeader
            ? <div style={{ display: "flex", gap: 8 }}>
                <Button size="sm" variant="primary" disabled={p.busy} onClick={() => p.onDecide(a, true)}>Approve this change</Button>
                <Button size="sm" variant="danger" disabled={p.busy} onClick={() => p.onDecide(a, false)}>Reject</Button>
              </div>
            : <p style={muted}>Only {view.leader ?? "a session leader"} can decide this.</p>)}
        </div>
      ))}

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
            <textarea aria-label="Direction for the agent" rows={3} value={content} onChange={(e) => setContent(e.target.value)} style={{ display: "block", width: "100%", margin: "6px 0" }} />
            {leaderOnly && <input aria-label="Reason for this change" placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} style={{ display: "block", width: "100%", marginBottom: 6 }} />}
            {leaderOnly && <p style={muted}>Changing scope or permissions voids open approvals; they must be requested again.</p>}
            <p style={muted}>The agent picks this up the next time it starts or resumes, not mid-step.</p>
            <Button type="submit" size="sm" variant="primary" disabled={p.busy || problem !== null}>Send direction</Button>
            {problem && content.length > 0 && <span role="note" style={{ ...muted, marginLeft: 8 }}>{problem}</span>}
          </form>}

      <h3 style={{ fontSize: 13 }}>Team history</h3>
      <ol role="log" aria-label="Collaboration history" style={{ margin: 0, paddingLeft: 18, maxHeight: 220, overflow: "auto" }}>
        {p.events.map((e) => (
          <li key={e.seq}><span style={muted}>{new Date(e.ts).toLocaleTimeString()}</span> {describeEvent(e)}</li>
        ))}
        {p.events.length === 0 && <li style={muted}>Nothing yet.</li>}
      </ol>
    </Card>
  );
}

const POLL_MS = 3000;

export function SharedRunPanel({ runId }: { runId: string }) {
  const session = loadSession();
  const token = session?.token ?? null;
  const me = session?.principal.userId ?? "";
  const canLead = session?.principal.role === "admin" || session?.principal.role === "engineer";
  const [roster, setRoster] = useState<Roster | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [events, setEvents] = useState<CollabEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lastSeq = useRef(0);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const [r, a, h] = await Promise.all([
        getRoster(runId, token), listApprovals(runId, token), getHistory(runId, token, lastSeq.current),
      ]);
      setRoster(r);
      setApprovals(a.approvals);
      if (h.events.length) {
        lastSeq.current = h.events[h.events.length - 1].seq;
        setEvents((prev) => [...prev, ...h.events]);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String((e as Error)?.message ?? e));
    }
  }, [runId, token]);

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const act = (fn: () => Promise<unknown>) => async () => {
    if (!token) return;
    setBusy(true);
    try { await fn(); setError(null); } catch (e) {
      setError(e instanceof ApiError ? e.message : String((e as Error)?.message ?? e));
    } finally { setBusy(false); await refresh(); }
  };
  const run = (fn: () => Promise<unknown>) => void act(fn)();

  if (!token) return null;
  const view = deriveCollabView(roster, approvals, me, canLead);
  return (
    <SharedRunView
      me={me} roster={roster} view={view} approvals={approvals} events={events}
      error={error} busy={busy}
      onJoin={() => run(() => joinRun(runId, token))}
      onClaim={() => run(() => claimLeader(runId, token))}
      onDecide={(a, ok) => run(() => decideApproval(runId, token, a, ok))}
      onHandoff={(to) => run(() => requestHandoff(runId, token, to))}
      onHandoffDecision={(id, ok) => run(() => decideHandoff(runId, token, id, ok))}
      onRedirect={(kind, content, reason) =>
        run(() => sendRedirect(runId, token, { kind, content, reason: reason || undefined, requestId: crypto.randomUUID() }))}
    />
  );
}

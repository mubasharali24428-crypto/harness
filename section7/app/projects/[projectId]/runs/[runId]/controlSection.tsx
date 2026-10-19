"use client";

// Part 42 UI: who holds control, suggestions waiting for the holder (accept / reject), the request queue
// and intent conflicts. Views are props-driven (static-render tested); RunControlSection fetches and acts.
import * as React from "react";
import { useCallback, useEffect, useRef } from "react";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import {
  type Conflict, type ConflictsView, type ControlView, type QueuedInstruction,
  decideControl, getConflicts, getControl, postHow, releaseControl, resolveConflict,
} from "./controlApi";
import { useAction, usePolled } from "./usePolled";

const muted = { color: "var(--muted)" } as const;
const h3 = { fontSize: 13 } as const;

export const expiryText = (h: NonNullable<ControlView["holder"]>): string => {
  if (h.untimed || h.expiresInSeconds === null) return "No expiry set.";
  if (h.expiresInSeconds < 60) return "Expires in under a minute.";
  return `Expires in about ${Math.ceil(h.expiresInSeconds / 60)} min.`;
};

export const fmtValue = (v: unknown): string =>
  v === null || v === undefined || v === "" ? "(none)"
    : Array.isArray(v) ? (v.length ? v.join(", ") : "(none)")
    : typeof v === "string" ? v : JSON.stringify(v);

/** Suggestions from non-holders. The holder decides; everyone else sees what is waiting and for whom. */
export function SuggestionsWaiting(p: {
  items: QueuedInstruction[]; me: string; holder: string | null; isHolder: boolean; busy: boolean;
  onDecide: (id: string, decision: "accept" | "reject") => void;
}) {
  const ids = React.useId();
  if (p.items.length === 0) return null;
  return (
    <section aria-label="Suggestions waiting" data-testid="suggestions-waiting">
      <h3 style={h3}>Suggestions waiting ({p.items.length})</h3>
      <p style={muted}>
        {p.isHolder
          ? "These reach the agent only if you accept them."
          : `${p.holder ?? "The person in control"} reviews these before the agent sees them.`}
      </p>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {p.items.map((s) => (
          <li key={s.id} data-testid={`suggestion-${s.id}`} style={{ marginBottom: 6 }}>
            <span id={`${ids}-${s.id}`}><strong>{s.author}{s.author === p.me ? " (you)" : ""}</strong> suggests: {s.content}</span>{" "}
            {p.isHolder
              ? <span role="group" aria-label="Decide on this suggestion">
                  <Button size="sm" variant="primary" disabled={p.busy} aria-describedby={`${ids}-${s.id}`} onClick={() => p.onDecide(s.id, "accept")}>Accept</Button>{" "}
                  <Button size="sm" variant="danger" disabled={p.busy} aria-describedby={`${ids}-${s.id}`} onClick={() => p.onDecide(s.id, "reject")}>Reject</Button>
                </span>
              : <span style={muted}>Waiting for review.</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ControlStatus(p: {
  control: ControlView; busy: boolean;
  onAcquire: () => void; onRequest: () => void; onRelease: () => void;
  onQueue: (requestId: string, decision: "grant" | "decline" | "cancel") => void;
}) {
  const { holder, viewer, queue } = p.control;
  return (
    <section aria-label="Control" data-testid="control-status">
      <h3 style={h3}>Control</h3>
      <p style={{ margin: "4px 0" }}>
        {holder
          ? <>Held by <strong>{holder.userId}</strong>{viewer.isHolder ? " (you)" : ""}. {expiryText(holder)}
              {holder.connected === false ? " They appear to be away." : ""}
              {holder.takeable ? " Control can be taken over." : ""}</>
          : "Nobody holds control."}
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {viewer.canAcquire && <Button size="sm" variant="primary" disabled={p.busy} onClick={p.onAcquire}>Take control</Button>}
        {viewer.canRequest && <Button size="sm" disabled={p.busy} onClick={p.onRequest}>Request control</Button>}
        {viewer.isHolder && <Button size="sm" disabled={p.busy} onClick={p.onRelease}>Release control</Button>}
      </div>
      {queue.length > 0 && (
        <ol aria-label="Control requests" style={{ margin: "6px 0 0", paddingLeft: 24 }}>
          {queue.map((q) => (
            <li key={q.requestId}>
              {q.userId}{q.userId === viewer.userId ? " (you)" : ""} is waiting for control.{" "}
              {viewer.isHolder && <>
                <Button size="sm" disabled={p.busy} onClick={() => p.onQueue(q.requestId, "grant")}>Hand over</Button>{" "}
                <Button size="sm" disabled={p.busy} onClick={() => p.onQueue(q.requestId, "decline")}>Decline</Button>
              </>}
              {q.userId === viewer.userId && <Button size="sm" disabled={p.busy} onClick={() => p.onQueue(q.requestId, "cancel")}>Cancel my request</Button>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** Base / current / proposed for each field two people changed at once. Nothing is merged automatically. */
export function ConflictList(p: {
  data: ConflictsView; canResolve: boolean; busy: boolean;
  onResolve: (c: Conflict, resolution: "keep_current" | "take_proposed") => void;
}) {
  const open = p.data.conflicts.filter((c) => c.state === "open");
  if (open.length === 0) return null;
  const voids = p.data.openApprovals.length;
  return (
    <section aria-label="Conflicting edits" data-testid="conflict-view">
      <h3 style={h3}>Conflicting edits ({open.length})</h3>
      {voids > 0 && <p style={muted}>Resolving a conflict that changes the plan voids {voids} open approval{voids === 1 ? "" : "s"}; they must be decided again.</p>}
      {open.map((c) => (
        <div key={c.id} role="group" aria-label={`${c.kind === "proposal" ? "Proposal" : "Conflict"} from ${c.author}`} data-testid={`conflict-${c.id}`}
          style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, marginBottom: 8 }}>
          <p style={{ margin: "0 0 4px" }}>
            {c.kind === "proposal" ? `${c.author} proposed a change.` : `${c.author} edited something that has since changed.`}
          </p>
          <table style={{ width: "100%", fontSize: 13 }}>
            <thead><tr><th scope="col" align="left">Field</th><th scope="col" align="left">Their starting point</th><th scope="col" align="left">Current</th><th scope="col" align="left">Their version</th></tr></thead>
            <tbody>
              {c.fields.map((f) => (
                <tr key={f.field}>
                  <th scope="row" align="left">{f.field}{f.conflicting ? " (clash)" : ""}</th>
                  <td>{fmtValue(f.base)}</td><td>{fmtValue(f.current)}</td><td>{fmtValue(f.proposed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {p.canResolve
            ? <div role="group" aria-label="Resolve" style={{ display: "flex", gap: 8, marginTop: 6 }}>
                <Button size="sm" disabled={p.busy} onClick={() => p.onResolve(c, "keep_current")}>Keep current</Button>
                <Button size="sm" variant="primary" disabled={p.busy} onClick={() => p.onResolve(c, "take_proposed")}>Use their version</Button>
              </div>
            : <p style={muted}>Only the person in control or a team admin can resolve this.</p>}
        </div>
      ))}
    </section>
  );
}

export function RunControlSection({ runId, token, tick, isAdmin, onChanged }: {
  runId: string; token: string; tick: number; isAdmin: boolean; onChanged: () => void;
}) {
  const [control, reloadControl] = usePolled(useCallback(() => getControl(runId, token), [runId, token]), tick, 5000);
  const [conflicts, reloadConflicts] = usePolled(useCallback(() => getConflicts(runId, token), [runId, token]), tick, 15000);
  const after = useCallback(async () => { await Promise.all([reloadControl(), reloadConflicts()]); onChanged(); }, [reloadControl, reloadConflicts, onChanged]);
  const { busy, error, said, setSaid, run } = useAction(after);

  // Announce a change of holder (polite live region).
  const holder = control ? (control.holder?.userId ?? null) : undefined;
  const prev = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (holder !== undefined && prev.current !== undefined && prev.current !== holder) {
      setSaid(holder ? `${holder} now holds control` : "Nobody holds control");
    }
    if (holder !== undefined) prev.current = holder;
  }, [holder, setSaid]);

  if (!control) return null;
  const isHolder = control.viewer.isHolder;
  return (
    <div data-testid="run-control">
      <div role="status" aria-live="polite" data-testid="control-live" style={{ position: "absolute", left: -9999 }}>{said}</div>
      {error && <Notice tone="danger" role="alert">{error}</Notice>}
      <SuggestionsWaiting
        items={control.queuedInstructions} me={control.viewer.userId} holder={control.holder?.userId ?? null}
        isHolder={isHolder} busy={busy}
        onDecide={(id, decision) => void run(() => decideControl(runId, token, { instructionId: id, decision }),
          () => (decision === "accept" ? "Suggestion accepted and sent to the agent" : "Suggestion rejected"))}
      />
      <ControlStatus
        control={control} busy={busy}
        onAcquire={() => void run(() => postHow(control.how.acquire, token), () => "You now hold control")}
        onRequest={() => void run(() => postHow(control.how.request, token), () => "Control requested")}
        onRelease={() => void run(() => releaseControl(runId, token), () => "You released control")}
        onQueue={(requestId, decision) => void run(() => decideControl(runId, token, { requestId, decision }))}
      />
      {conflicts && (
        <ConflictList
          data={conflicts} canResolve={isHolder || isAdmin} busy={busy}
          onResolve={(c, resolution) => void run(
            () => resolveConflict(runId, token, c.id, resolution, conflicts.currentApproved.version),
            () => "Conflict resolved; open approvals must be decided again")}
        />
      )}
    </div>
  );
}

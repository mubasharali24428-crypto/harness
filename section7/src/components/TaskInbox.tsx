"use client";

// A4-S4 (F005 mobile task inbox): queued issue/PR intakes with human approve/reject.
// Intake text is untrusted: rendered as text only, links limited to http(s). Approving
// records a decision; it never starts a run.
import * as React from "react";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import type { Intake, IntakeApi } from "@/lib/intakeApi";

export const safeHref = (u: string): string | null => /^https?:\/\//i.test(u) ? u : null;
export const preview = (s: string, n = 160): string => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

const CSS = `
.ti{display:grid;gap:12px;padding:0;margin:0;list-style:none}
.ti li{border:1px solid #30363d;border-radius:8px;padding:12px;display:grid;gap:8px}
.ti-actions{display:flex;gap:8px;flex-wrap:wrap}
.ti-actions button{min-height:44px;min-width:44px;padding:0 16px;font-size:1rem}
@media (max-width:640px){.ti-actions button{flex:1}}
`;

export function TaskInboxView(props: {
  items: Intake[];
  canDecide: boolean;
  busyId: string | null;
  error: string | null;
  notice: string | null;
  onDecide: (id: string, approve: boolean) => void;
}) {
  const pending = props.items.filter((i) => i.status === "pending_approval");
  return (
    <section aria-labelledby="inbox-title" data-testid="task-inbox">
      <style>{CSS}</style>
      <h2 id="inbox-title">Task inbox</h2>
      <p aria-live="polite" data-testid="inbox-summary">{pending.length} waiting for a decision</p>
      <p>Approving records your decision. It does not start a run.</p>
      {props.error && <p role="alert" data-testid="inbox-error">{props.error}</p>}
      {props.notice && <p role="status" data-testid="inbox-notice">{props.notice}</p>}
      {props.items.length === 0 ? <p>No queued tasks.</p> : (
        <ul className="ti">
          {props.items.map((i) => {
            const href = safeHref(i.url);
            return (
              <li key={i.id} data-testid="inbox-item" data-status={i.status}>
                <strong>{i.title || "(untitled)"}</strong>
                <span>{i.provider} · {i.kind} · from {i.actor || "unknown"} · {i.status.replace("_", " ")}</span>
                <span>{preview(i.body)}</span>
                {href && <a href={href} rel="noopener noreferrer" target="_blank">Open original</a>}
                {i.status === "pending_approval" && props.canDecide && (
                  <div className="ti-actions">
                    <button type="button" disabled={props.busyId === i.id} onClick={() => props.onDecide(i.id, true)}
                      aria-label={`Approve ${i.title}`}>Approve</button>
                    <button type="button" disabled={props.busyId === i.id} onClick={() => props.onDecide(i.id, false)}
                      aria-label={`Reject ${i.title}`}>Reject</button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {!props.canDecide && <p>Your role can view the inbox but not decide.</p>}
    </section>
  );
}

export function TaskInbox({ api, canDecide }: { api: IntakeApi; canDecide: boolean }) {
  const [items, setItems] = useState<Intake[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setItems(await api.list());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the inbox.");
    }
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  const onDecide = async (id: string, approve: boolean) => {
    setBusyId(id);
    setNotice(null);
    try {
      const out = await api.decide(id, approve);
      setNotice(approve ? "Approved and recorded. No run was started." : "Rejected.");
      setItems((cur) => cur.map((x) => (x.id === id ? { ...x, status: out.status } : x)));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The decision failed.");
    } finally {
      setBusyId(null);
    }
  };
  return <TaskInboxView items={items} canDecide={canDecide} busyId={busyId} error={error} notice={notice} onDecide={onDecide} />;
}

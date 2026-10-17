"use client";

// A4-S4 (F005 mobile task inbox): queued issue/PR intakes with human approve/reject.
// Intake text is untrusted: rendered as text only, links limited to http(s). Approving
// records a decision; it never starts a run.
import * as React from "react";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { makeT, type Translate } from "@/lib/i18n";
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
  t?: Translate;
}) {
  const t = props.t ?? makeT("en");
  const pending = props.items.filter((i) => i.status === "pending_approval");
  return (
    <section aria-labelledby="inbox-title" data-testid="task-inbox">
      <style>{CSS}</style>
      <h2 id="inbox-title">{t("inbox.title")}</h2>
      <p aria-live="polite" data-testid="inbox-summary">{t("inbox.waiting", { n: pending.length })}</p>
      <p>{t("inbox.noRun")}</p>
      {props.error && <p role="alert" data-testid="inbox-error">{props.error}</p>}
      {props.notice && <p role="status" data-testid="inbox-notice">{props.notice}</p>}
      {props.items.length === 0 ? <p>{t("inbox.empty")}</p> : (
        <ul className="ti">
          {props.items.map((i) => {
            const href = safeHref(i.url);
            return (
              <li key={i.id} data-testid="inbox-item" data-status={i.status}>
                <strong>{i.title || "(untitled)"}</strong>
                <span>{i.provider} · {i.kind} · from {i.actor || "unknown"} · {i.status.replace("_", " ")}</span>
                <span>{preview(i.body)}</span>
                {href && <a href={href} rel="noopener noreferrer" target="_blank">{t("inbox.open")}</a>}
                {i.status === "pending_approval" && props.canDecide && (
                  <div className="ti-actions">
                    <button type="button" disabled={props.busyId === i.id} onClick={() => props.onDecide(i.id, true)}
                      aria-label={t("inbox.approveLabel", { title: i.title })}>{t("inbox.approve")}</button>
                    <button type="button" disabled={props.busyId === i.id} onClick={() => props.onDecide(i.id, false)}
                      aria-label={t("inbox.rejectLabel", { title: i.title })}>{t("inbox.reject")}</button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {!props.canDecide && <p>{t("inbox.readOnly")}</p>}
    </section>
  );
}

export function TaskInbox({ api, canDecide, t = makeT("en") }: { api: IntakeApi; canDecide: boolean; t?: Translate }) {
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
      setNotice(t(approve ? "inbox.approved" : "inbox.rejected"));
      setItems((cur) => cur.map((x) => (x.id === id ? { ...x, status: out.status } : x)));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The decision failed.");
    } finally {
      setBusyId(null);
    }
  };
  return <TaskInboxView items={items} canDecide={canDecide} busyId={busyId} error={error} notice={notice} onDecide={onDecide} t={t} />;
}

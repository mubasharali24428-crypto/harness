"use client";

// Part 47 preferences, presentational half (hook-free): scope picker (global / workspace / session),
// channels, per-event table with the effective result, quiet hours with time zone, digest, mutes.
import * as React from "react";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { formatDateTime, hourLabel } from "@/lib/intl";
import { KINDS, type Mute, type Prefs } from "../../notifications/notificationsApi";
import {
  EMAIL_LABEL, MUTE_OPTIONS, type PrefsForm, kindLabel, muteLabel, quietProblem, scopeProblem,
} from "../../notifications/notificationsModel";

const muted = { color: "var(--muted)" } as const;
const field = { display: "block", margin: "6px 0" } as const;
const legend = { fontSize: 13, fontWeight: 600 } as const;

export type ScopeChoice = { kind: "global" | "workspace" | "session"; id: string };
export interface PrefsActions {
  setScope: (s: ScopeChoice) => void;
  loadScope: () => void;
  change: (patch: Partial<PrefsForm>) => void;
  changeEvent: (kind: string, patch: Partial<{ inapp: string; email: string }>) => void;
  save: () => void;
  mute: (kind: "workspace" | "session" | "run" | "kind", id: string, minutes?: number) => void;
  unmute: (m: Mute) => void;
  setDraft: (d: MuteDraft) => void;
}
export interface MuteDraft { kind: "kind" | "run" | "session" | "workspace"; id: string; duration: string }
export const emptyDraft: MuteDraft = { kind: "kind", id: "approval", duration: "60" };

const InappSelect = ({ value, inherit, onChange, label }: { value: string; inherit: boolean; onChange: (v: string) => void; label: string }) => (
  <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
    {inherit && <option value="">Inherit</option>}
    <option value="on">{inherit ? "On" : "On (default)"}</option>
    <option value="off">Off</option>
  </select>
);
const EmailSelect = ({ value, inherit, onChange, label }: { value: string; inherit: boolean; onChange: (v: string) => void; label: string }) => (
  <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
    {inherit && <option value="">Inherit</option>}
    {(Object.keys(EMAIL_LABEL) as (keyof typeof EMAIL_LABEL)[]).map((m) => <option key={m} value={m}>{EMAIL_LABEL[m]}{!inherit && m === "off" ? " (default)" : ""}</option>)}
  </select>
);

export function PrefsView(p: {
  scope: ScopeChoice; prefs: Prefs | null; form: PrefsForm | null; zones: string[]; draft: MuteDraft;
  busy: boolean; dirty: boolean; error: string | null; saved: string | null; actions: PrefsActions; locale?: string;
}) {
  const a = p.actions;
  const global = p.scope.kind === "global";
  const scopeBad = global ? null : scopeProblem(p.scope.kind as "workspace" | "session", p.scope.id);
  const f = p.form;
  const qProblem = f && global ? quietProblem(f) : null;
  return (
    <div data-testid="prefs">
      <p style={muted}>
        Notifications never make a sound, open a pop-up or take focus. They are counted on the bell and listed in your inbox.
        Email is off unless you turn it on here. More specific settings win: session, then workspace, then your global settings.
      </p>
      {p.error && <Notice tone="danger" role="alert">{p.error}</Notice>}
      {p.saved && <Notice tone="success" role="status">{p.saved}</Notice>}

      <fieldset style={{ border: "1px solid var(--border)", borderRadius: 6, margin: "8px 0" }}>
        <legend style={legend}>Which settings</legend>
        <div role="radiogroup" aria-label="Settings scope" style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
          {(["global", "workspace", "session"] as const).map((k) => (
            <label key={k}><input type="radio" name="scope" checked={p.scope.kind === k} onChange={() => a.setScope({ kind: k, id: k === "global" ? "" : p.scope.id })} />{" "}
              {k === "global" ? "Everything (global)" : k === "workspace" ? "One workspace" : "One session"}</label>
          ))}
        </div>
        {!global && (
          <div style={{ marginTop: 6 }}>
            <label>{p.scope.kind === "workspace" ? "Workspace" : "Session"} id{" "}
              <input value={p.scope.id} onChange={(e) => a.setScope({ kind: p.scope.kind, id: e.target.value })} maxLength={80} aria-invalid={scopeBad !== null && p.scope.id.length > 0 || undefined} />
            </label>{" "}
            <Button size="sm" disabled={p.busy || scopeBad !== null} onClick={a.loadScope}>Load these settings</Button>
            {scopeBad && p.scope.id.length > 0 && <span role="status" style={{ ...muted, marginLeft: 8 }}>{scopeBad}</span>}
          </div>
        )}
      </fieldset>

      {!f && <p style={muted}>{global ? "Loading…" : "Enter an id and load its settings."}</p>}
      {f && (
        <form onSubmit={(e) => { e.preventDefault(); a.save(); }}>
          <fieldset style={{ border: "1px solid var(--border)", borderRadius: 6, margin: "8px 0" }}>
            <legend style={legend}>Channels{global ? "" : ` for this ${p.scope.kind}`}</legend>
            <label style={field}>In the app{" "}<InappSelect label="In the app" value={f.inapp} inherit={!global} onChange={(v) => a.change({ inapp: v })} /></label>
            <label style={field}>Email{" "}<EmailSelect label="Email" value={f.email} inherit={!global} onChange={(v) => a.change({ email: v })} /></label>
          </fieldset>

          <table style={{ width: "100%", fontSize: 13, margin: "8px 0" }}>
            <caption style={{ textAlign: "left", ...legend }}>By type of event</caption>
            <thead><tr><th scope="col" align="left">Type</th><th scope="col" align="left">In the app</th><th scope="col" align="left">Email</th><th scope="col" align="left">Result now</th></tr></thead>
            <tbody>
              {KINDS.map((k) => {
                const eff = p.prefs?.effective?.[k];
                return (
                  <tr key={k} data-testid={`event-${k}`}>
                    <th scope="row" align="left">{kindLabel(k)}</th>
                    <td><InappSelect label={`${kindLabel(k)}: in the app`} value={f.events[k].inapp} inherit onChange={(v) => a.changeEvent(k, { inapp: v })} /></td>
                    <td><EmailSelect label={`${kindLabel(k)}: email`} value={f.events[k].email} inherit onChange={(v) => a.changeEvent(k, { email: v })} /></td>
                    <td style={muted}>{eff ? `In the app ${eff.inapp}, email ${eff.email === "off" ? "off" : EMAIL_LABEL[eff.email as keyof typeof EMAIL_LABEL]?.toLowerCase() ?? eff.email}` : "Saved settings show here after loading"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {global && (
            <>
              <fieldset style={{ border: "1px solid var(--border)", borderRadius: 6, margin: "8px 0" }}>
                <legend style={legend}>Quiet hours</legend>
                <p style={muted}>Quiet hours hold email only. Urgent items still get through. Your inbox is always silent.</p>
                <label style={field}><input type="checkbox" checked={f.quietEnabled} onChange={(e) => a.change({ quietEnabled: e.target.checked })} /> Hold email during quiet hours</label>
                <label style={field}>From{" "}<input type="time" value={f.quietStart} disabled={!f.quietEnabled} onChange={(e) => a.change({ quietStart: e.target.value })} aria-invalid={qProblem !== null || undefined} /></label>
                <label style={field}>Until{" "}<input type="time" value={f.quietEnd} disabled={!f.quietEnabled} onChange={(e) => a.change({ quietEnd: e.target.value })} aria-invalid={qProblem !== null || undefined} /></label>
                <label style={field}>Time zone{" "}
                  <input list="tz-list" value={f.timezone} onChange={(e) => a.change({ timezone: e.target.value })} aria-describedby="tz-help" />
                </label>
                <datalist id="tz-list">{p.zones.map((z) => <option key={z} value={z} />)}</datalist>
                <p id="tz-help" style={{ ...muted, fontSize: 12 }}>A name like Europe/London. Quiet hours are read in this zone.</p>
                {qProblem && <Notice tone="danger" role="status">{qProblem}</Notice>}
              </fieldset>
              <fieldset style={{ border: "1px solid var(--border)", borderRadius: 6, margin: "8px 0" }}>
                <legend style={legend}>Email digest</legend>
                <p style={muted}>Used for events you set to &quot;In a digest&quot;: one email listing what is still unread.</p>
                <label style={field}>How often{" "}
                  <select value={f.digestPeriod} onChange={(e) => a.change({ digestPeriod: e.target.value })}>
                    <option value="daily">Daily</option><option value="weekly">Weekly</option>
                  </select>
                </label>
                <label style={field}>After{" "}
                  <select value={f.digestHour} onChange={(e) => a.change({ digestHour: Number(e.target.value) })}>
                    {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hourLabel(h, { locale: p.locale })}</option>)}
                  </select>{" "}in your time zone
                </label>
              </fieldset>
            </>
          )}
          <Button type="submit" variant="primary" disabled={p.busy || !p.dirty || qProblem !== null}>Save settings</Button>
          {!p.dirty && <span style={{ ...muted, marginLeft: 8 }}>No changes to save.</span>}
        </form>
      )}

      {global && p.prefs && (
        <section aria-label="Muted" style={{ marginTop: 16 }}>
          <h3 style={{ fontSize: 13 }}>Muted</h3>
          <p style={muted}>A mute silences non-urgent items from that source. Urgent items still reach your inbox.</p>
          {(p.prefs.mutes ?? []).length === 0 && <p style={muted}>Nothing is muted.</p>}
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {(p.prefs.mutes ?? []).map((m) => (
              <li key={m.id} data-testid={`mute-${m.id}`}>
                {muteLabel(m)}, {m.until ? `until ${formatDateTime(m.until, { locale: p.locale })}` : "until you unmute"}.{" "}
                <Button size="sm" disabled={p.busy} aria-label={`Unmute ${muteLabel(m)}`} onClick={() => a.unmute(m)}>Unmute</Button>
              </li>
            ))}
          </ul>
          <div role="group" aria-label="Mute something" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end", marginTop: 8 }}>
            <label>Mute{" "}
              <select value={p.draft.kind} onChange={(e) => a.setDraft({ kind: e.target.value as MuteDraft["kind"], id: e.target.value === "kind" ? "approval" : "", duration: p.draft.duration })}>
                <option value="kind">A type of event</option><option value="run">One run</option><option value="session">One session</option><option value="workspace">One workspace</option>
              </select>
            </label>
            {p.draft.kind === "kind"
              ? <label>Type{" "}<select value={p.draft.id} onChange={(e) => a.setDraft({ ...p.draft, id: e.target.value })}>{KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}</select></label>
              : <label>Id{" "}<input value={p.draft.id} maxLength={80} onChange={(e) => a.setDraft({ ...p.draft, id: e.target.value })} /></label>}
            <label>For{" "}
              <select value={p.draft.duration} onChange={(e) => a.setDraft({ ...p.draft, duration: e.target.value })}>
                {MUTE_OPTIONS.map((o) => <option key={o.label} value={o.minutes ?? ""}>{o.label}</option>)}
              </select>
            </label>
            <Button size="sm" disabled={p.busy || !p.draft.id.trim()} onClick={() => a.mute(p.draft.kind, p.draft.id.trim(), p.draft.duration ? Number(p.draft.duration) : undefined)}>Mute</Button>
          </div>
        </section>
      )}
    </div>
  );
}

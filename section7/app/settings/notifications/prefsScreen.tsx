"use client";

// Part 47 preferences, container half. Saves a merge-patch of ONLY what changed, carrying the version that
// was read; a 409 reloads the server's copy and says so instead of overwriting it.
import * as React from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { getToken } from "@/lib/auth";
import { useAnnouncer } from "@/components/LiveAnnouncer";
import { timeZones } from "@/lib/intl";
import {
  type Prefs, getPrefs, getScopePrefs, muteScope, notificationError, putPrefs, putScopePrefs, unmute,
} from "../../notifications/notificationsApi";
import { type PrefsForm, formFromPrefs, isDirty, patchFromForm } from "../../notifications/notificationsModel";
import { type MuteDraft, type PrefsActions, type ScopeChoice, PrefsView, emptyDraft } from "./prefsView";

export function PrefsScreen() {
  const { announce, region } = useAnnouncer();
  const [token, setToken] = useState<string | null>(null);
  const [scope, setScope] = useState<ScopeChoice>({ kind: "global", id: "" });
  const [loaded, setLoaded] = useState<ScopeChoice | null>(null); // the scope the form below belongs to
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [orig, setOrig] = useState<PrefsForm | null>(null);
  const [form, setForm] = useState<PrefsForm | null>(null);
  const [draft, setDraft] = useState<MuteDraft>(emptyDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const zones = useMemo(() => timeZones(), []);

  useEffect(() => setToken(getToken()), []);

  const fetchScope = useCallback(async (s: ScopeChoice) => {
    if (!token) return;
    setBusy(true);
    try {
      const p = s.kind === "global" ? await getPrefs(token) : await getScopePrefs(token, s.kind, s.id.trim());
      const f = formFromPrefs(p);
      setPrefs(p); setOrig(f); setForm(f); setLoaded(s); setError(null);
    } catch (e) { setError(notificationError(e)); } finally { setBusy(false); }
  }, [token]);
  useEffect(() => { if (token) void fetchScope({ kind: "global", id: "" }); }, [token, fetchScope]);

  const patch = prefs && orig && form && loaded ? patchFromForm(orig, form, loaded.kind, prefs.version) : {};
  const dirty = isDirty(patch);

  const actions: PrefsActions = {
    setScope: (s) => {
      setScope(s); setSaved(null);
      if (s.kind === "global") { void fetchScope(s); } else if (loaded?.kind !== s.kind) { setPrefs(null); setForm(null); setOrig(null); setLoaded(null); }
    },
    loadScope: () => void fetchScope(scope),
    change: (c) => setForm((f) => (f ? { ...f, ...c } : f)),
    changeEvent: (k, c) => setForm((f) => (f ? { ...f, events: { ...f.events, [k]: { ...f.events[k], ...c } } } : f)),
    save: () => {
      if (!token || !loaded || !dirty) return;
      setBusy(true);
      const go = loaded.kind === "global" ? putPrefs(token, patch) : putScopePrefs(token, loaded.kind, loaded.id.trim(), patch);
      go.then((p) => {
        const f = formFromPrefs(p);
        setPrefs((old) => ({ ...p, mutes: p.mutes ?? old?.mutes })); setOrig(f); setForm(f); setError(null);
        setSaved("Settings saved."); announce("Settings saved");
      }, (e) => {
        setError(notificationError(e)); setSaved(null);
        if (e?.code === "VERSION_CONFLICT") void fetchScope(loaded); // show the server's copy; nothing is overwritten
      }).finally(() => setBusy(false));
    },
    mute: (kind, id, minutes) => {
      if (!token) return;
      setBusy(true);
      muteScope(token, { scopeKind: kind, scopeId: id, minutes }).then(
        async () => { announce("Muted"); setSaved("Muted."); await fetchScope(loaded ?? { kind: "global", id: "" }); },
        (e) => setError(notificationError(e)),
      ).finally(() => setBusy(false));
    },
    unmute: (m) => {
      if (!token) return;
      setBusy(true);
      unmute(token, m.id).then(
        async () => { announce("Unmuted"); setSaved("Unmuted."); await fetchScope(loaded ?? { kind: "global", id: "" }); },
        (e) => setError(notificationError(e)),
      ).finally(() => setBusy(false));
    },
    setDraft,
  };

  const shown = loaded !== null && loaded.kind === scope.kind && (scope.kind === "global" || loaded.id.trim() === scope.id.trim());
  if (!token) return <p style={{ color: "var(--muted)" }}>Sign in to change your notification settings.</p>;
  return (
    <>
      {region}
      <PrefsView scope={scope} prefs={shown ? prefs : null} form={shown ? form : null} zones={zones}
        draft={draft} busy={busy} dirty={dirty} error={error} saved={saved} actions={actions} />
    </>
  );
}

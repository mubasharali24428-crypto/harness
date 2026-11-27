"use client";

// Parts 31/58: /settings/account, where a person erases their own account. The server decides
// (confirmed email, not the last admin, no held run); this page only asks and reports.

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { BreadcrumbTrail, settingsCrumbs } from "@/components/Breadcrumbs";
import { loadSession } from "@/lib/auth";
import { runErasure } from "./accountApi";
import { ErasureCard } from "./erasureCard";
import { getRendering, putRendering, type Rendering } from "./renderingApi";
import { RenderingCard } from "./renderingCard";

export default function AccountSettingsPage() {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const token = loadSession()?.token;
  const [rendering, setRendering] = useState<Rendering>("technical");
  const [renderingBusy, setRenderingBusy] = useState(false);
  const [renderingNotice, setRenderingNotice] = useState<string | null>(null);
  const [renderingError, setRenderingError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getRendering(token)
      .then((res) => setRendering(res.rendering))
      .catch((cause: unknown) => setRenderingError(String((cause as Error)?.message ?? cause)));
  }, [token]);

  const saveRendering = async () => {
    if (!token) return;
    setRenderingBusy(true);
    setRenderingNotice(null);
    setRenderingError(null);
    try {
      await putRendering(token, rendering);
      setRenderingNotice("Rendering saved. This changes only how approvals are shown.");
    } catch (cause) {
      setRenderingError(String((cause as Error)?.message ?? cause));
    } finally {
      setRenderingBusy(false);
    }
  };

  const submit = async () => {
    setBusy(true);
    setProblem(null);
    const out = await runErasure(typed, loadSession()?.token);
    setProblem(out.problem);
    setDone(out.done);
    setBusy(false);
  };

  return (
    <AppShell active="/settings">
      <BreadcrumbTrail items={settingsCrumbs("Your account")} />
      <h1>Your account</h1>
      {token && (
        <RenderingCard
          value={rendering}
          onChange={setRendering}
          onSave={saveRendering}
          busy={renderingBusy}
          notice={renderingNotice}
          error={renderingError}
        />
      )}
      <ErasureCard typed={typed} onType={setTyped} onSubmit={submit} busy={busy} problem={problem} done={done} />
    </AppShell>
  );
}

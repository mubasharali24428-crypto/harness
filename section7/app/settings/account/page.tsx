"use client";

// Parts 31/58: /settings/account, where a person erases their own account. The server decides
// (confirmed email, not the last admin, no held run); this page only asks and reports.

import { useState } from "react";
import AppShell from "@/components/AppShell";
import { BreadcrumbTrail, settingsCrumbs } from "@/components/Breadcrumbs";
import { loadSession } from "@/lib/auth";
import { runErasure } from "./accountApi";
import { ErasureCard } from "./erasureCard";

export default function AccountSettingsPage() {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState(false);

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
      <ErasureCard typed={typed} onType={setTyped} onSubmit={submit} busy={busy} problem={problem} done={done} />
    </AppShell>
  );
}

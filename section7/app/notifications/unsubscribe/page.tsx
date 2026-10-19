"use client";

// Email unsubscribe landing page (docs/notifications.md: the emailed token is the credential, no login).
// Opening the page changes nothing; the person confirms with a button.
import * as React from "react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { unsubscribe } from "../notificationsApi";
import { ApiError } from "@/lib/api";

export default function UnsubscribePage() {
  const [token, setToken] = useState<string | null>(null);
  const [state, setState] = useState<"idle" | "busy" | "done" | "again" | "gone" | "error">("idle");
  useEffect(() => setToken(new URLSearchParams(window.location.search).get("token")), []);
  const go = async () => {
    if (!token) return;
    setState("busy");
    try { const r = await unsubscribe(token); setState(r.alreadyApplied ? "again" : "done"); }
    catch (e) { setState(e instanceof ApiError && e.status === 404 ? "gone" : "error"); }
  };
  return (
    <main style={{ maxWidth: 520, margin: "48px auto", padding: "0 16px" }}>
      <h1>Stop notification emails</h1>
      {!token && <Notice tone="danger" role="alert">This link is missing its code. Open the link from the email again.</Notice>}
      {token && state === "idle" && <>
        <p>This turns off email from Harness for your account, including any per-project or per-event email settings. You can turn it back on under Settings, Notifications. Your in-app notifications are not affected.</p>
        <Button variant="primary" onClick={() => void go()}>Stop these emails</Button>
      </>}
      {state === "busy" && <p role="status">Working…</p>}
      {state === "done" && <Notice tone="success" role="status">Done. You will not get notification emails any more.</Notice>}
      {state === "again" && <Notice tone="info" role="status">This link was already used. Nothing was changed.</Notice>}
      {state === "gone" && <Notice tone="danger" role="alert">This link is not valid any more (it expires after 90 days). Change email settings under Settings, Notifications instead.</Notice>}
      {state === "error" && <Notice tone="danger" role="alert">Something went wrong. Try again in a moment.</Notice>}
    </main>
  );
}

"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";

export default function Login() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const principal = await api.login(email);
      localStorage.setItem("harness.principal", JSON.stringify(principal));
      if (!localStorage.getItem("harness.userId"))
        localStorage.setItem("harness.userId", principal.userId);
      if (!localStorage.getItem("harness.projectId"))
        localStorage.setItem("harness.projectId", "proj-test-1");
      if (!localStorage.getItem("harness.runId"))
        localStorage.setItem("harness.runId", "run-ui-1");
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      <form onSubmit={submit} className="card" style={{ width: 360 }}>
        <h2 style={{ color: "var(--accent)", letterSpacing: 2 }}>HARNESS</h2>
        <p>Sign in to your team workspace. New email? We create your solo team.</p>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@team.dev"
          type="email"
          required
          style={{
            width: "100%",
            background: "var(--panel-2)",
            border: "1px solid var(--border)",
            color: "var(--text)",
            borderRadius: 6,
            padding: 10,
            marginBottom: 10,
          }}
        />
        <button className="btn primary" disabled={busy} style={{ width: "100%" }}>
          {busy ? "Signing in…" : "Enter the harness"}
        </button>
        {error && (
          <p style={{ color: "var(--red)", marginTop: 8 }}>
            {error} — is the identity service up on :8001?
          </p>
        )}
      </form>
    </div>
  );
}

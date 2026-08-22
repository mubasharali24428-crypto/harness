"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { login, ApiError } from "@/lib/api";
import { saveSession } from "@/lib/auth";
import { inputStyle, buttonStyle } from "@/lib/styles";

export default function HomePage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [teamName, setTeamName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [runId, setRunId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const { token, principal } = await login(email, teamName);
      saveSession(token, principal);
      router.push(`/projects/${projectId}/runs/${runId}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ maxWidth: 420, margin: "80px auto", padding: 24 }}>
      <h1 style={{ fontSize: 20, marginBottom: 4 }}>Working Area</h1>
      <p style={{ color: "#9aa4b2", fontSize: 13, marginBottom: 24 }}>
        Section 9&apos;s shared shell and project picker don&apos;t exist yet — enter a
        project and run id directly (e.g. seeded via the backend for testing).
      </p>
      <form onSubmit={handleSubmit} style={{ display: "grid", gap: 12 }}>
        <label>
          Email
          <input value={email} onChange={(e) => setEmail(e.target.value)} required style={inputStyle} />
        </label>
        <label>
          Team name (blank = new solo team)
          <input value={teamName} onChange={(e) => setTeamName(e.target.value)} style={inputStyle} />
        </label>
        <label>
          Project ID
          <input
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            required
            style={inputStyle}
          />
        </label>
        <label>
          Run ID
          <input value={runId} onChange={(e) => setRunId(e.target.value)} required style={inputStyle} />
        </label>
        {error && <p style={{ color: "#f87171" }}>{error}</p>}
        <button type="submit" disabled={busy} style={buttonStyle}>
          {busy ? "Signing in…" : "Enter Working Area"}
        </button>
      </form>
    </main>
  );
}

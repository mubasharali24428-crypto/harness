"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, login, requestLoginOtp, verifyLoginOtp } from "@/lib/api";
import { saveSession, setProjectId, setRunId, setUserId } from "@/lib/auth";
import type { Principal, Role } from "@contracts/roles";

type Mode = "legacy" | "otp-code-sent";

export default function Login() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [mode, setMode] = useState<Mode>("legacy");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  function adoptSession(token: string, principal: Principal) {
    saveSession(token, principal);
    setUserId(principal.userId);
    if (!setProjectIdIfEmpty()) setProjectId("proj-test-1");
    if (!localStorage.getItem("harness.runId")) setRunId(`run-${Date.now().toString(36)}`);
    router.push("/");
  }

  function setProjectIdIfEmpty(): boolean {
    if (localStorage.getItem("harness.projectId")) return true;
    return false;
  }

  async function legacySubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { token, principal } = await login(email);
      adoptSession(token, principal);
    } catch (err) {
      // Flag-aware: when the server enforces ownership proof (403 OWNERSHIP_REQUIRED),
      // switch to the OTP flow instead of dead-ending.
      if (err instanceof ApiError && err.code === "OWNERSHIP_REQUIRED") {
        await startOtpFlow();
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function startOtpFlow() {
    try {
      await requestLoginOtp(email);
      setMode("otp-code-sent");
      setNote(
        `We emailed a 6-digit code to ${email}. It expires in 10 minutes — check spam too.`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function otpSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { token, principal } = await verifyLoginOtp(email, code.trim());
      // S1 provisions roles from the same enum; the DTO's role is a plain string
      // on the wire — assert it into the frozen union at the boundary.
      adoptSession(token, { ...principal, role: principal.role as Role });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      {mode === "legacy" ? (
        <form onSubmit={legacySubmit} className="card" style={{ width: 360 }}>
          <h2 style={{ color: "var(--accent)", letterSpacing: 2 }}>HARNESS</h2>
          <p>Sign in to your team workspace. New email? We create your solo team.</p>
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@team.dev"
            type="email"
            required
            className="text-input"
          />
          <button className="btn primary" disabled={busy} style={{ width: "100%" }}>
            {busy ? "Signing in…" : "Enter the harness"}
          </button>
          {error && (
            <p style={{ color: "var(--red)", marginTop: 8 }}>{error}</p>
          )}
        </form>
      ) : (
        <form onSubmit={otpSubmit} className="card" style={{ width: 360 }}>
          <h2 style={{ color: "var(--accent)", letterSpacing: 2 }}>HARNESS</h2>
          <p>{note}</p>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="123456"
            inputMode="numeric"
            pattern="[0-9]*"
            maxLength={6}
            autoComplete="one-time-code"
            required
            className="text-input"
            style={{ letterSpacing: 8, textAlign: "center", fontSize: 16 }}
          />
          <button className="btn primary" disabled={busy || code.length !== 6} style={{ width: "100%" }}>
            {busy ? "Verifying…" : "Verify code"}
          </button>
          <button
            type="button"
            className="btn"
            style={{ width: "100%", marginTop: 6 }}
            disabled={busy}
            onClick={() => {
              setMode("legacy");
              setError(null);
              setNote(null);
              setCode("");
            }}
          >
            ← Back
          </button>
          {error && (
            <p style={{ color: "var(--red)", marginTop: 8 }}>{error}</p>
          )}
        </form>
      )}
    </div>
  );
}

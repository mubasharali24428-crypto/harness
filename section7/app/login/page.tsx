"use client";

// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too — same as meeting/page.
import * as React from "react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, login, requestLoginOtp, verifyLoginOtp } from "@/lib/api";
import { saveSession, setUserId } from "@/lib/auth";
// W1-C2 (C-006): post-auth identity is resolved for real — the caller's actual
// project (or an honest route to creating one). No fabricated fallbacks.
// (Relative import: the @/* alias maps into src/, this lane owns lib/ at root.)
import {
  bootstrapProjectSession,
  bootstrapSucceeded,
  createAndActivateProject,
} from "../../lib/session";
import type { Principal, Role } from "@contracts/roles";

type Mode = "legacy" | "otp-code-sent" | "create-project";

export default function Login() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [mode, setMode] = useState<Mode>("legacy");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // M12-E2: /login?expired=1 — the apiFetch interceptor lands here after a 401.
  const [sessionExpired, setSessionExpired] = useState(false);
  const [projectName, setProjectName] = useState("");
  // Token held while the user names their first project (create-project mode).
  const [pendingToken, setPendingToken] = useState<string | null>(null);

  useEffect(() => {
    // Read the query param off window (not useSearchParams) so the statically
    // prerendered login page never needs a Suspense boundary.
    if (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("expired") === "1") {
      setSessionExpired(true);
    }
  }, []);

  /**
   * C-006 — post-auth identity is REAL or the flow routes to creation:
   *  - a stored-and-still-valid projectId is kept;
   *  - otherwise the first project of the caller's team is adopted;
   *  - with zero projects the page switches to the create-project flow and
   *    only continues once POST /api/v1/projects returns a real row.
   * No run id is minted client-side anymore: runs come into existence when
   * the orchestrator's plan gate mints them server-side on approve.
   */
  async function adoptSession(token: string, principal: Principal): Promise<void> {
    saveSession(token, principal);
    setUserId(principal.userId);

    let boot = await bootstrapProjectSession(token);
    if (!bootstrapSucceeded(boot.outcome)) {
      if (boot.outcome === "needs-project") {
        setPendingToken(token);
        setMode("create-project");
        return; // continue after a real project exists
      }
      throw new Error(
        "Could not load your projects (is the identity service up?). Nothing was invented to fill in.",
      );
    }

    router.push("/");
  }

  async function createProjectSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!pendingToken) return;
    setBusy(true);
    setError(null);
    try {
      await createAndActivateProject(projectName, pendingToken);
      setPendingToken(null);
      setMode("legacy");
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function legacySubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { token, principal } = await login(email);
      await adoptSession(token, principal);
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
      await adoptSession(token, { ...principal, role: principal.role as Role });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      {sessionExpired && (
        // M12-E2: shown when the 401 interceptor bounces here with ?expired=1.
        <div
          role="alert"
          style={{
            position: "fixed",
            top: 16,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 10,
            background: "#3b2f0b",
            color: "#f5d76e",
            border: "1px solid #8a7500",
            borderRadius: 6,
            padding: "10px 18px",
            fontSize: 14,
          }}
        >
          Session expired — sign in again.
        </div>
      )}
      {mode === "legacy" ? (
        <form onSubmit={legacySubmit} className="card login-card">
          <h2 style={{ color: "var(--accent)", letterSpacing: 2 }}>HARNESS</h2>
          <p>Sign in to your team workspace. New email? We create your solo team.</p>
          {/* M15-S3 (W4-VT2 carryover): placeholder-only naming fails WCAG
              4.1.2/3.3.2 — real <label for>, visually hidden via .sr-only so
              the label-free design stays intact. */}
          <label htmlFor="login-email" className="sr-only">
            Email address
          </label>
          <input
            id="login-email"
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
      ) : mode === "create-project" ? (
        // C-006: honest first-run state — the account has no projects yet, so
        // we create a real one (POST /api/v1/projects) before entering.
        <form onSubmit={createProjectSubmit} className="card login-card">
          <h2 style={{ color: "var(--accent)", letterSpacing: 2 }}>HARNESS</h2>
          <p>
            Your team has no projects yet. Name your first one — it&apos;s created for real in
            Section 1 before you continue.
          </p>
          {/* M15-S3: accessible name for the placeholder-only input. */}
          <label htmlFor="login-project-name" className="sr-only">
            Project name
          </label>
          <input
            id="login-project-name"
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            placeholder="Project name (e.g. Atlas migration)"
            required
            className="text-input"
          />
          <button
            className="btn primary"
            disabled={busy || !projectName.trim()}
            style={{ width: "100%" }}
          >
            {busy ? "Creating project…" : "Create project & enter"}
          </button>
          {error && (
            <p style={{ color: "var(--red)", marginTop: 8 }}>{error}</p>
          )}
        </form>
      ) : (
        <form onSubmit={otpSubmit} className="card login-card">
          <h2 style={{ color: "var(--accent)", letterSpacing: 2 }}>HARNESS</h2>
          <p>{note}</p>
          {/* M15-S3: accessible name for the placeholder-only OTP input. */}
          <label htmlFor="login-otp-code" className="sr-only">
            6-digit code
          </label>
          <input
            id="login-otp-code"
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

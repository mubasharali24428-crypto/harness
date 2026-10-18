"use client";

// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too — same as meeting/page.
import * as React from "react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, login, requestLoginOtp, ssoStart, verifyLoginOtp } from "@/lib/api";
import { saveSession, setUserId } from "@/lib/auth";
// W1-C2 (C-006): post-auth identity is resolved for real — the caller's actual
// project (or an honest route to creating one). No fabricated fallbacks.
// (Relative import: the @/* alias maps into src/, this lane owns lib/ at root.)
import {
  bootstrapProjectSession,
  bootstrapSucceeded,
  createAndActivateProject,
} from "../../lib/session";
// F-gap closure (external-validation §SSO): the caller half of the OIDC
// redirect — stash + send off; the callback replays the exact redirect_uri.
import { savePendingSso, ssoRedirectUri } from "../../lib/sso";
// M16-X4 (raw-17): markup migrated onto the shared DS primitives — Button /
// Brand / Notice replace the inline-styled controls, wordmark and tinted
// status lines. Visual parity intent; all ids/classes/testids preserved.
// Brand/Notice are X4-PROPOSED additions (new files in ui/): ui/index.ts is
// the DS lane's frozen barrel, so they are consumed by module path until the
// DS lane adopts and re-exports them.
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { Brand } from "@/components/ui/Brand";
import type { Principal, Role } from "@contracts/roles";

type Mode = "legacy" | "otp-code-sent" | "create-project" | "sso";

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
  // F-gap closure: team id for the SSO entry (mode === "sso").
  const [teamId, setTeamId] = useState("");
  const [ready, setReady] = useState(false);
  useEffect(() => { setReady(true); }, []);
  // Token held while the user names their first project (create-project mode).
  const [pendingToken, setPendingToken] = useState<string | null>(null);
  // M19-N4: ?next=demo — the canned-demo entry sent strangers here to
  // bootstrap a (key-free) identity first; remember to send them back.
  const [nextDemo, setNextDemo] = useState(false);

  useEffect(() => {
    // Read query params off window (not useSearchParams) so the statically
    // prerendered login page never needs a Suspense boundary.
    const params = new URLSearchParams(window.location.search);
    if (params.get("expired") === "1") {
      setSessionExpired(true);
    }
    if (params.get("next") === "demo") {
      setNextDemo(true);
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

    // M19-N4: the demo entry sent this stranger here for a key-free identity;
    // honor the round-trip instead of dropping them in the empty dashboard.
    if (nextDemo && typeof window !== "undefined") {
      router.push("/demo");
      return;
    }

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

  /**
   * F-gap closure: team SSO entry. start() mints state/PKCE server-side and
   * returns the IdP authorization URL; the exact redirect_uri used here is
   * stashed and replayed by /auth/sso/callback (Section 1 binds it per
   * attempt). Unknown/SSO-less teams answer 404 SSO_NOT_CONFIGURED — shown
   * as-is; the email form stays the honest fallback.
   */
  async function ssoSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const trimmed = teamId.trim();
      const redirectUri = ssoRedirectUri(window.location.origin);
      const { authorizationUrl } = await ssoStart(trimmed, redirectUri);
      savePendingSso(window.sessionStorage, { teamId: trimmed, redirectUri });
      window.location.assign(authorizationUrl);
      // Navigation is underway; keep busy=true so nothing double-fires.
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      {sessionExpired && (
        // M12-E2: shown when the 401 interceptor bounces here with ?expired=1.
        // M16-X4: Notice(boxed) carries the banner face verbatim (role=alert).
        <Notice boxed role="alert" testId="login-session-expired">
          Session expired — sign in again.
        </Notice>
      )}
      {mode === "legacy" ? (
        <form onSubmit={legacySubmit} className="card login-card">
          <Brand>HARNESS</Brand>
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
          {/* M16-X4: Button(md) keeps the .btn box at the form-input rhythm.
              EXCEPTION (documented): width:100% rides Button's style prop —
              the frozen primitive has no fullWidth prop and ui/ is consume-
              only; flagged for the DS lane as a candidate prop. */}
          <Button type="submit" variant="primary" size="md" disabled={busy || !ready} loading={busy} loadingText="Signing in…" style={{ width: "100%" }}>
            {busy ? "Signing in…" : "Enter the harness"}
          </Button>
          {/* M19-N4 (C10): the stranger's zero-key path — bootstrap an identity,
              then straight into the canned replay. testId pinned by tests. */}
          <Button
            type="button"
            variant="secondary"
            size="md"
            disabled={busy}
            onClick={() => router.push("/demo")}
            style={{ width: "100%", marginTop: 6 }}
          >
            Try the demo →
          </Button>
          {/* F-gap closure: SSO entry toggle — the native flow for teams
              with an IdP configured in Section 1. */}
          <Button
            type="button"
            variant="secondary"
            size="md"
            disabled={busy}
            onClick={() => {
              setMode("sso");
              setError(null);
            }}
            style={{ width: "100%", marginTop: 6 }}
          >
            Sign in with team SSO →
          </Button>
          {error && <Notice tone="danger" style={{ marginTop: 8 }}>{error}</Notice>}
        </form>
      ) : mode === "create-project" ? (
        // C-006: honest first-run state — the account has no projects yet, so
        // we create a real one (POST /api/v1/projects) before entering.
        <form onSubmit={createProjectSubmit} className="card login-card">
          <Brand>HARNESS</Brand>
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
          {/* M16-X4: EXCEPTION — width:100% via Button's style prop (no
              fullWidth in the frozen contract; see legacy-mode note). */}
          <Button
            type="submit"
            variant="primary"
            size="md"
            disabled={busy || !projectName.trim()}
            style={{ width: "100%" }}
          >
            {busy ? "Creating project…" : "Create project & enter"}
          </Button>
          {error && <Notice tone="danger" style={{ marginTop: 8 }}>{error}</Notice>}
        </form>
      ) : mode === "sso" ? (
        // F-gap closure: binds the SPA callback route as redirect_uri, then
        // hands the browser to the IdP; /auth/sso/callback finishes the swap.
        <form onSubmit={ssoSubmit} className="card login-card">
          <Brand>HARNESS</Brand>
          <p>
            Sign in through your team&apos;s identity provider. Ask a team admin
            if you don&apos;t know your team ID.
          </p>
          {/* M15-S3 pattern: accessible name for the placeholder-only input. */}
          <label htmlFor="login-team-id" className="sr-only">
            Team ID
          </label>
          <input
            id="login-team-id"
            value={teamId}
            onChange={(e) => setTeamId(e.target.value)}
            placeholder="team-id"
            required
            className="text-input"
          />
          {/* M16-X4: EXCEPTION — width:100% via Button's style prop (no
              fullWidth in the frozen contract; see legacy-mode note). */}
          <Button
            type="submit"
            variant="primary"
            size="md"
            disabled={busy || !teamId.trim()}
            style={{ width: "100%" }}
          >
            {busy ? "Redirecting…" : "Continue with SSO"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="md"
            disabled={busy}
            onClick={() => {
              setMode("legacy");
              setError(null);
            }}
            style={{ width: "100%", marginTop: 6 }}
          >
            ← Back
          </Button>
          {error && <Notice tone="danger" style={{ marginTop: 8 }}>{error}</Notice>}
        </form>
      ) : (
        <form onSubmit={otpSubmit} className="card login-card">
          <Brand>HARNESS</Brand>
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
          {/* M16-X4: EXCEPTION — width:100% via Button's style prop (no
              fullWidth in the frozen contract; see legacy-mode note). */}
          <Button
            type="submit"
            variant="primary"
            size="md"
            disabled={busy || code.length !== 6}
            style={{ width: "100%" }}
          >
            {busy ? "Verifying…" : "Verify code"}
          </Button>
          {/* M16-X4: the ← Back control is a plain secondary Button; the
              marginTop nudge is the one-off spacing exception (above). */}
          <Button
            type="button"
            variant="secondary"
            size="md"
            disabled={busy}
            onClick={() => {
              setMode("legacy");
              setError(null);
              setNote(null);
              setCode("");
            }}
            style={{ width: "100%", marginTop: 6 }}
          >
            ← Back
          </Button>
          {error && <Notice tone="danger" style={{ marginTop: 8 }}>{error}</Notice>}
        </form>
      )}
    </div>
  );
}

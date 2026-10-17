"use client";

// F-gap closure (docs/runbooks/external-validation.md §SSO): /auth/sso/callback
// — where the IdP sends the browser after POST /api/v1/auth/sso/start. Echoes
// code+state back to complete with the EXACT redirectUri the attempt was bound
// to (lib/sso.ts stash), stores the session, then lands on the dashboard like
// every other auth path. Failures render an honest Notice — nothing invented.
import * as React from "react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ssoComplete } from "@/lib/api";
import { saveSession, setUserId } from "@/lib/auth";
import {
  bootstrapProjectSession,
  bootstrapSucceeded,
} from "../../../../lib/session";
import {
  clearPendingSso,
  readPendingSso,
  readSsoCallbackParams,
} from "../../../../lib/sso";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { Brand } from "@/components/ui/Brand";

type Phase = "completing" | "error";

export default function SsoCallback() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("completing");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const params = readSsoCallbackParams(window.location.search);
        if (params.error) {
          throw new Error(
            `The identity provider declined the sign-in (${params.error}). Start again from the sign-in page.`,
          );
        }
        const pending = readPendingSso(window.sessionStorage);
        if (!pending || !params.code || !params.state) {
          throw new Error(
            "No pending SSO attempt was found in this browser. Start again from the sign-in page.",
          );
        }
        const { token, principal } = await ssoComplete(
          pending.teamId,
          params.code,
          params.state,
          pending.redirectUri,
        );
        if (cancelled) return;
        saveSession(token, principal);
        setUserId(principal.userId);
        clearPendingSso(window.sessionStorage);
        const boot = await bootstrapProjectSession(token);
        if (cancelled) return;
        // Same rule as the login page (C-006): a real project or an honest
        // empty state; only a failed project LIST blocks the landing.
        if (!bootstrapSucceeded(boot.outcome) && boot.outcome !== "needs-project") {
          throw new Error(
            "Signed in, but your projects could not be loaded. Refresh to retry.",
          );
        }
        router.push("/");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setPhase("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      <div className="card login-card" data-testid="sso-callback">
        <Brand>HARNESS</Brand>
        {phase === "completing" ? (
          <p role="status">Completing sign-in…</p>
        ) : (
          <>
            <Notice tone="danger" role="alert" testId="sso-callback-error">
              {error}
            </Notice>
            {/* width:100% via Button's style prop — documented exception (no
                fullWidth in the frozen primitive; see login page notes). */}
            <Button
              type="button"
              variant="secondary"
              size="md"
              onClick={() => router.push("/login")}
              style={{ width: "100%" }}
            >
              Back to sign-in
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

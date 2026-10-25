"use client";

// Settings -> Models: the team's durable choice of primary/reviewer model and
// reasoning effort. Backed by Section 3's /models/prefs (migration 0062);
// admins can change it, every member can see what will run.

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { BreadcrumbTrail, settingsCrumbs } from "@/components/Breadcrumbs";
import { ModelPrefsForm } from "@/components/ModelPrefs";
import { loadSession } from "@/lib/auth";
import { panelStyle } from "@/lib/styles";

export default function ModelsSettingsPage() {
  const [token, setToken] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);

  useEffect(() => {
    const session = loadSession();
    setToken(session?.token ?? null);
    setRole(session?.principal.role ?? null);
  }, []);

  return (
    <AppShell active="/settings">
      <BreadcrumbTrail items={settingsCrumbs("Models")} />
      <h1>Models</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, maxWidth: 640 }}>
        Which model plans and reviews work for this team, and how much reasoning
        the provider should spend. Runs use the primary model; router calls use
        the model for their role.
      </p>

      <section style={{ ...panelStyle, maxWidth: 700 }} data-testid="models-settings">
        {token ? (
          <ModelPrefsForm token={token} role={role} variant="full" />
        ) : (
          <p style={{ color: "var(--muted)", fontSize: 13 }}>Sign in to manage models.</p>
        )}
      </section>

      <p style={{ color: "var(--muted)", fontSize: 12, maxWidth: 700 }}>
        Provider credentials live in <a href="/settings/keys" style={{ color: "var(--accent)" }}>API keys</a>.
        A model must be served by the configured provider endpoint; choosing one
        the endpoint does not serve makes the run fail with the provider&apos;s
        error instead of silently switching models.
      </p>
    </AppShell>
  );
}

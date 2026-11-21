"use client";

/**
 * P-17 client trust portal: what a partner's end-client sees after signing in.
 * Lists the projects an admin granted them and each project's runs; a run opens
 * the read-only report. Nothing here writes. Access is decided by the backend
 * (guest_gate): this page only asks for what a guest is allowed to read.
 */
import { useEffect, useState } from "react";
import { clearSession, loadSession } from "@/lib/auth";
import { apiFetch, IDENTITY_BASE } from "@/lib/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { runRowTitle } from "@/components/workspace/previewData";
import { listRuns, stateLabel, type RunDto } from "../runs/runsApi";

interface PortalProject {
  id: string;
  name: string;
  runs: RunDto[];
}

export default function PortalPage() {
  const [projects, setProjects] = useState<PortalProject[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = loadSession()?.token;
    if (!token) {
      window.location.assign("/login");
      return;
    }
    (async () => {
      const { items } = await apiFetch<{ items: { id: string; name: string }[] }>(
        `${IDENTITY_BASE}/api/v1/portal/projects`, token,
      );
      // ponytail: newest 50 runs per project, no paging; add "older runs" when a client has more.
      setProjects(await Promise.all(items.map(async (p) => ({
        ...p, runs: (await listRuns({ projectId: p.id, limit: 50 }, token)).items,
      }))));
    })().catch((e: unknown) => setError(e instanceof Error ? e.message : "Something went wrong."));
  }, []);

  return (
    <main className="ws-report" data-testid="portal">
      <p className="ws-report-kicker">Read-only client portal · every change, who approved it, its tests and its cost</p>
      <h1>Your projects</h1>
      {error ? (
        <ErrorState title="The portal didn’t load" message={error} />
      ) : projects === null ? (
        <LoadingState label="Loading your projects…" rows={4} />
      ) : projects.length === 0 ? (
        <EmptyState title="No projects are shared with you yet" body="Ask your contact to grant you access to a project." />
      ) : (
        projects.map((p) => (
          <section key={p.id} data-testid="portal-project">
            <h2>{p.name}</h2>
            {p.runs.length === 0 ? (
              <p className="ws-evidence-note">No runs yet.</p>
            ) : (
              <ol>
                {p.runs.map((r) => (
                  <li key={r.id}>
                    <a href={`/projects/${p.id}/runs/${r.id}/report`}>{runRowTitle(r.goal)}</a>
                    {" · "}{stateLabel(r.status)}{" · "}{new Date(r.createdAt).toLocaleDateString()}
                  </li>
                ))}
              </ol>
            )}
          </section>
        ))
      )}
      <p className="ws-evidence-links">
        <button className="ws-foot-link" onClick={() => { clearSession(); window.location.assign("/login"); }}>Sign out</button>
      </p>
    </main>
  );
}

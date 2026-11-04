"use client";

// Mission 10 node B3 — /onboarding: the revived first-run flow.
// Renders Section 10's GET /api/v1/onboarding/checklist ({id,title,description?,done})
// as an interactive checklist with a n/m progress bar and a manual refresh.
// Real fetches through ./onboardingApi.ts against the Section 10 service — no
// mocks, no fake completion states: a checkmark appears only when a real
// completion signal exists. Inline errors use role="alert".
//
// M12-E7 (raw-04 M1/M2) — auto-complete + per-step actions:
//  * Completion is DERIVED from live cross-section signals (github connection,
//    team/members, keys count, runs total — see ./onboardingDerived.ts), so a
//    step flips to done when its real-world condition exists, not just when the
//    backend records it.
//  * Signals are re-read on mount + every window/page focus (cheap single GETs;
//    no interval timers). The "Updated" line makes data age visible.
//  * Each not-done step deep-links to the surface where the work happens.

import { useCallback, useEffect, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import { ErrorState } from "@/components/States";
import { loadErrorMessage } from "@/lib/humanize";
import { getProjectId, getToken, loadSession } from "@/lib/auth";
import { type ReadinessReport, getReadiness } from "@/lib/api";
import {
  ChecklistItemDto,
  completedCount,
  formatProgressLabel,
  getChecklist,
  progressPct,
} from "./onboardingApi";
import {
  EMPTY_SIGNALS,
  type SignalState,
  activeMilestones,
  deriveItems,
  fetchGithubConnected,
  fetchKeysCount,
  fetchMembersCount,
  fetchRunsTotal,
} from "./onboardingDerived";
import { ChecklistRows, SignalsCard } from "./checklistView";
import { ReadinessCard } from "./readinessView";

const cardStyle = { maxWidth: 760, marginBottom: 20 } as const;

export default function OnboardingPage() {
  // null = nothing loaded yet (distinct from an empty checklist).
  const [items, setItems] = useState<ChecklistItemDto[] | null>(null);
  const [signals, setSignals] = useState<SignalState>(EMPTY_SIGNALS);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // M12-E7: null = never refreshed; else epoch ms of the last successful load.
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  // W23: readiness is per project; its failure never fails the checklist.
  const [projectId, setProjectId] = useState<string | null>(null);
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const inFlight = useRef(false);

  /**
   * One refresh pass: server checklist + all cheap signal probes, in parallel.
   * Guarded against overlap (rapid focus flips can fire more than mount/focus
   * alone) — a pass while one is running is dropped, not queued. Signal probe
   * failures resolve to null inside onboardingDerived and render honestly as
   * unknown; they must never fail the whole page.
   */
  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const token = getToken();
      const teamId = loadSession()?.principal.teamId;
      const project = getProjectId();
      setProjectId(project);
      if (project) {
        getReadiness(project, token)
          .then((r) => {
            setReadiness(r);
            setReadinessError(null);
          })
          .catch((e) => {
            setReadiness(null);
            setReadinessError(loadErrorMessage(e));
          });
      }
      const [dto, githubConnected, membersCount, keysCount, runsTotal] = await Promise.all([
        getChecklist(), // no auth today — see note in onboardingApi.ts
        fetchGithubConnected(token),
        teamId ? fetchMembersCount(teamId, token) : Promise.resolve(null),
        fetchKeysCount(teamId, token),
        fetchRunsTotal(token),
      ]);
      setItems(dto.items);
      setSignals({ teamExists: teamId ? true : null, membersCount, githubConnected, keysCount, runsTotal });
      setUpdatedAt(Date.now());
    } catch (e) {
      setError(loadErrorMessage(e));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    // raw-04 M1: poll cheaply on focus, never on an interval. VisibilityChange
    // covers tab switches; FocusEvent covers returning to an already-visible
    // tab/window. Both funnel into the same overlap-guarded pass above.
    const onFocus = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [refresh]);

  // Derive completion from live signals over whatever rows exist. Unknown
  // signals fall back to the server verdict (see deriveItemDone).
  const derived = items ? deriveItems(items, signals) : null;

  const done = derived ? completedCount(derived) : 0;
  const total = derived?.length ?? 0;
  const pct = derived ? progressPct(derived) : 0;
  const milestones = activeMilestones(signals);

  return (
    <AppShell active="/onboarding">
      <h1>Onboarding</h1>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        First-run checklist, in the frozen brief order. A step completes itself the moment its
        real-world condition exists — connect GitHub, add a key, or start a run elsewhere and
        this list catches up when you come back. Nothing here pretends progress: an unreadable
        signal shows as unknown instead of done.
      </p>

      {error && (
        <ErrorState
          title="The checklist didn’t load"
          message={error}
          onRetry={() => void refresh()}
        />
      )}

      <section className="card" style={cardStyle} aria-busy={busy}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <h2 style={{ margin: 0 }}>Getting started</h2>
          <button
            type="button"
            className="btn"
            data-testid="checklist-refresh"
            disabled={busy}
            onClick={() => refresh()}
            aria-label="Refresh checklist"
          >
            {busy ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        <p style={{ margin: "10px 0 0", color: "var(--muted)", fontSize: 13 }}>
          Auto-updates when you return to this page.{" "}
          {updatedAt ? (
            <span data-testid="onboarding-updated-at">Updated {new Date(updatedAt).toLocaleTimeString()}</span>
          ) : (
            <span data-testid="onboarding-updated-at">Not loaded yet</span>
          )}
        </p>

        <p
          data-testid="onboarding-progress-label"
          style={{ margin: "10px 0 6px", fontWeight: 600 }}
        >
          {formatProgressLabel(derived ?? [])}
        </p>

        {/* Meter renders only with real rows behind it — an empty/late-seeding
            checklist must not declare aria-valuemax=0 (raw-04 L7 edge). */}
        {total > 0 && (
          <div
            role="progressbar"
            aria-label="Onboarding progress"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={done}
            style={{
              height: 8,
              borderRadius: 4,
              border: "1px solid var(--border)",
              background: "var(--panel-2)",
              overflow: "hidden",
              marginBottom: 16,
            }}
          >
            <div
              style={{
                width: `${pct}%`,
                height: "100%",
                background: "var(--green)",
                transition: "width 150ms ease",
              }}
            />
          </div>
        )}

        {!derived && !error && <p style={{ color: "var(--muted)" }}>Loading checklist…</p>}

        {derived !== null && <ChecklistRows items={derived} />}

        {derived !== null && derived.length === 0 && (
          <p style={{ color: "var(--muted)" }}>
            The backend returned no checklist items — the onboarding service may still be seeding
            its steps.
          </p>
        )}
      </section>

      <ReadinessCard projectId={projectId} report={readiness} error={readinessError} />

      <SignalsCard signals={signals} />

      <section className="card" style={cardStyle}>
        <h2>Milestones</h2>
        <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
          Extra first-run goals this page can verify from live data.
        </p>
        {milestones.length === 0 ? (
          <p style={{ color: "var(--muted)" }} data-testid="milestones-empty">
            No extra milestones reached yet — add a provider key under Settings → API keys, or
            complete your first run from the Command Center.
          </p>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18 }} data-testid="milestones-list">
            {milestones.includes("api_key_added") && (
              <li>
                Provider API key added ✅ — manage keys any time under{" "}
                <a href="/settings/keys" style={{ color: "var(--accent)", textDecoration: "underline" }}>
                  Settings → API keys
                </a>
                .
              </li>
            )}
            {milestones.includes("first_run") && (
              <li>
                First run recorded ✅ — history lives on{" "}
                <a href="/runs" style={{ color: "var(--accent)", textDecoration: "underline" }}>
                  Runs
                </a>
                .
              </li>
            )}
          </ul>
        )}
      </section>

      <section className="card" style={cardStyle}>
        <h2>What each step means</h2>
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          Connect GitHub (Section 2) → other integrations (none required for the MVP) → create a
          team and invite members (Section 1) → start a meeting (Section 6, not yet deployed) → run
          the guided demo end to end (Section 10). Steps owned by sections that are not deployed
          yet stay honestly unchecked instead of dead-ending your setup.
        </p>
      </section>
    </AppShell>
  );
}

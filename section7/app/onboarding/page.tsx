"use client";

// Mission 10 node B3 — /onboarding: the revived first-run flow.
// Renders Section 10's GET /api/v1/onboarding/checklist ({id,title,description?,done})
// as an interactive checklist with a n/m progress bar and a manual refresh.
// Real fetches through ./onboardingApi.ts against the Section 10 service — no
// mocks, no fake completion states: a checkmark appears only when the backend
// reports `done`. Inline errors use role="alert".
// Nav link into this page is wired by ATLAS at merge (AppShell is shared code).

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import {
  ChecklistItemDto,
  completedCount,
  formatProgressLabel,
  getChecklist,
  progressPct,
} from "./onboardingApi";

const cardStyle = { maxWidth: 760, marginBottom: 20 } as const;

export default function OnboardingPage() {
  // null = nothing loaded yet (distinct from an empty checklist).
  const [items, setItems] = useState<ChecklistItemDto[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const dto = await getChecklist();
      setItems(dto.items);
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const done = items ? completedCount(items) : 0;
  const total = items?.length ?? 0;
  const pct = items ? progressPct(items) : 0;

  return (
    <AppShell active="/onboarding">
      <h1>Onboarding</h1>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        First-run checklist, in the frozen brief order. A step shows its checkmark only once the
        backend records a real completion signal — nothing here pretends progress.
      </p>

      {error && (
        <div
          role="alert"
          data-testid="onboarding-error"
          className="pill red"
          style={{ padding: "8px 12px", marginBottom: 16 }}
        >
          {error}
        </div>
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

        <p
          data-testid="onboarding-progress-label"
          style={{ margin: "10px 0 6px", fontWeight: 600 }}
        >
          {formatProgressLabel(items ?? [])}
        </p>
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

        {!items && !error && <p style={{ color: "var(--muted)" }}>Loading checklist…</p>}

        {items !== null && (
          <ol style={{ listStyle: "none", margin: 0, padding: 0 }} data-testid="checklist-items">
            {items.map((item, index) => (
              <li
                key={item.id}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  padding: "10px 0",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <span aria-hidden="true" style={{ fontSize: 18, lineHeight: "22px" }}>
                  {item.done ? "✅" : "⬜"}
                </span>
                <div style={{ flex: 1 }}>
                  <div>
                    <strong>{`${index + 1}. ${item.title}`}</strong>{" "}
                    <span
                      className={item.done ? "pill green" : "pill blue"}
                      style={{ marginLeft: 6 }}
                    >
                      {item.done ? "Done" : "To do"}
                    </span>
                  </div>
                  {item.description && (
                    <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 2 }}>
                      {item.description}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}

        {items !== null && items.length === 0 && (
          <p style={{ color: "var(--muted)" }}>
            The backend returned no checklist items — the onboarding service may still be seeding
            its steps.
          </p>
        )}
      </section>

      <section className="card" style={cardStyle}>
        <h2>What each step means</h2>
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          Connect GitHub (Section 2) → other integrations (none required for the MVP) → create a
          team and invite members (Section 1) → start a meeting (Section 6, not yet deployed) → run
          the guided demo end to end (Section 10). Steps owned by sections that are not deployed yet
          stay honestly unchecked instead of dead-ending your setup.
        </p>
      </section>
    </AppShell>
  );
}

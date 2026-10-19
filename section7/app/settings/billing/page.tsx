"use client";

// Mission 10 node B2 — /settings/billing: client of contract M10-C3.
// Shows the current plan and a quota meter (usedUsd vs monthlyBudgetUsd);
// admins can change the plan via PUT /api/v1/billing/plan with inline
// feedback. When billing is disabled (enabled=false / source:"flag-off")
// this renders an honest upsell card — no error states, no crashes.

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { loadSession } from "@/lib/auth";
import {
  BILLING_PLANS,
  BillingStateDto,
  TeamBillingLimitsDto,
  formatUsd,
  isOverBudget,
  meterPct,
  updatePlan,
  updateBillingLimits,
  getBillingState,
} from "./billingApi";

const meterOuterStyle = {
  height: 10,
  borderRadius: 999,
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  overflow: "hidden",
} as const;

const selectStyle = {
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: 6,
  padding: "6px 8px",
} as const;

function pctLabel(pct: number): string {
  const rounded = Math.round(pct * 10) / 10;
  return Number.isInteger(rounded) ? `${rounded}%` : `${rounded.toFixed(1)}%`;
}

export default function BillingSettingsPage() {
  const [state, setState] = useState<BillingStateDto | null>(null);
  const [limits, setLimits] = useState<TeamBillingLimitsDto | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Admin-only plan change; "" while untouched so admins see "change…" first.
  const [selectedPlan, setSelectedPlan] = useState("");

  const refresh = useCallback(async () => {
    const token = loadSession()?.token ?? "";
    const billing = await getBillingState(token);
    setState(billing);
    setLimits(billing.limits ?? null);
  }, []);

  useEffect(() => {
    if (!loadSession()) {
      setError("Not signed in — log in first.");
      return;
    }
    refresh().catch((e: unknown) => setError(String((e as Error)?.message ?? e)));
  }, [refresh]);

  const onChangePlan = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await updatePlan(selectedPlan, loadSession()?.token ?? "");
      setState(updated);
      setSelectedPlan("");
      setNotice(`Plan changed to ${updated.plan}.`);
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const onSaveLimits = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!limits) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await updateBillingLimits(
        {
          monthlyBudgetUsd: Number(limits.monthlyBudgetUsd),
          maxConcurrentRuns: Number(limits.maxConcurrentRuns),
          budgetAlertPercent: Number(limits.budgetAlertPercent),
        },
        loadSession()?.token ?? "",
      );
      setState(saved);
      setLimits(saved.limits ?? null);
      setNotice("Team spend and concurrency limits saved and audited.");
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const enabled = state?.enabled === true;
  // M10-C3 flag-off payload is {enabled:false, plan:null, quota:null,
  // source:"flag-off"} — the quota helpers below only ever run when the
  // fetched state actually carries a quota, so they can't crash on null.
  const quota = state?.quota ?? null;
  const overBudget = quota !== null && isOverBudget(quota);
  const pct = quota !== null ? meterPct(quota) : 0;
  const canManageBilling = limits?.canManage ?? loadSession()?.principal.role === "admin";

  const meterColor = overBudget ? "var(--red)" : "var(--green)";

  return (
    <AppShell active="/settings">
      <h1>Billing</h1>

      {state === null && !error && (
        <p style={{ color: "var(--muted)" }}>Loading billing state…</p>
      )}

      {error && (
        <div role="alert" className="pill red" style={{ padding: "8px 12px", marginBottom: 16 }}>
          {error}
        </div>
      )}
      {notice && (
        <div role="status" style={{ color: "var(--green)", marginBottom: 16 }}>
          {notice}
        </div>
      )}

      {!enabled && state !== null && (
        <section
          className="card"
          data-testid="billing-upsell"
          style={{ maxWidth: 760, marginBottom: 20 }}
        >
          <h2>Billing not enabled on this deployment</h2>
          <p style={{ color: "var(--muted)" }}>
            Usage tracking and paid plans are switched off here. Ask your deployment operator to
            enable billing (set the HARNESS_BILLING flag) to manage plans and monthly budgets.
          </p>
        </section>
      )}

      {enabled && limits && canManageBilling && (
        <section className="card" style={{ maxWidth: 760, marginBottom: 20 }}>
          <h2>Team usage limits</h2>
          <p style={{ color: "var(--muted)" }}>
            These limits apply even while Stripe billing is disabled. A $0 monthly budget blocks
            provider spend. Set the alert to 0 to disable budget warnings.
          </p>
          <form onSubmit={onSaveLimits} style={{ display: "grid", gap: 12, maxWidth: 440 }}>
            <label>
              Monthly spend hard limit (USD)
              <input
                aria-label="Monthly spend hard limit in USD"
                type="number"
                min="0"
                step="0.01"
                required
                value={limits.monthlyBudgetUsd}
                disabled={busy}
                onChange={(event) => setLimits({ ...limits, monthlyBudgetUsd: Number(event.target.value) })}
              />
            </label>
            <label>
              Maximum concurrent runs
              <input
                aria-label="Maximum concurrent runs"
                type="number"
                min="1"
                max="100"
                step="1"
                required
                value={limits.maxConcurrentRuns}
                disabled={busy}
                onChange={(event) => setLimits({ ...limits, maxConcurrentRuns: Number(event.target.value) })}
              />
            </label>
            <label>
              Budget warning threshold (%)
              <input
                aria-label="Budget warning threshold percent"
                type="number"
                min="0"
                max="99.99"
                step="0.01"
                required
                value={limits.budgetAlertPercent}
                disabled={busy}
                onChange={(event) => setLimits({ ...limits, budgetAlertPercent: Number(event.target.value) })}
              />
            </label>
            <button type="submit" className="btn primary" disabled={busy}>Save usage limits</button>
          </form>
        </section>
      )}

      {enabled && state !== null && (
        <>
          <section className="card" style={{ maxWidth: 760, marginBottom: 20 }}>
            <h2>Current plan</h2>
            <p data-testid="billing-plan" style={{ fontSize: 16 }}>
              <strong>{state.plan ?? "unset"}</strong>{" "}
              <span className="pill blue">{state.source}</span>
            </p>
          </section>

          {/* M10-C3 flag-off answers quota:null — every quota-field read below
              sits behind this state.quota check, so the meter branch is
              unreachable while quota is null. */}
          {enabled && state !== null && state.quota !== null && (
            <section className="card" style={{ maxWidth: 760, marginBottom: 20 }}>
              <h2>Monthly usage</h2>
              {/* role="progressbar" needs an accessible name — aria-label provides it */}
              <div
                role="progressbar"
                aria-label="Monthly spend against budget"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(pct)}
                aria-valuetext={`${pctLabel(pct)} of budget used`}
              >
                <div style={meterOuterStyle}>
                  <div
                    style={{
                      width: `${pct}%`,
                      height: "100%",
                      background: meterColor,
                      transition: "width 200ms ease",
                    }}
                  />
                </div>
              </div>
              <p
                style={{
                  margin: "8px 0 0",
                  color: overBudget ? "var(--red)" : "var(--text)",
                  fontSize: 13,
                }}
              >
                {formatUsd(state.quota.usedUsd)} used of {formatUsd(state.quota.monthlyBudgetUsd)}{" "}
                budget ({pctLabel(pct)}) —{" "}
                <span style={{ color: meterColor }}>
                  {overBudget ? "over budget" : "within budget"}
                </span>
              </p>
            </section>
          )}

          {canManageBilling ? (
            <section className="card" style={{ maxWidth: 760, marginBottom: 20 }}>
              <h2>Change plan</h2>
              <form onSubmit={onChangePlan} style={{ display: "flex", gap: 8 }}>
                <label htmlFor="billing-plan-select" style={{ alignSelf: "center" }}>
                  Plan
                </label>
                <select
                  id="billing-plan-select"
                  value={selectedPlan}
                  onChange={(e) => setSelectedPlan(e.target.value)}
                  disabled={busy}
                  style={selectStyle}
                >
                  <option value="">change…</option>
                  {Array.from(new Set([state.plan ?? undefined, ...BILLING_PLANS]))
                    .filter((p): p is string => typeof p === "string")
                    .map((plan) => (
                      <option key={plan} value={plan}>
                        {plan}
                      </option>
                    ))}
                </select>
                <button type="submit" className="btn primary" disabled={busy || selectedPlan === ""}>
                  Apply
                </button>
              </form>
              <p style={{ color: "var(--muted)", fontSize: 12, marginTop: 8 }}>
                Plan changes apply to the whole team and take effect immediately.
              </p>
            </section>
          ) : (
            <section className="card" style={{ maxWidth: 760, marginBottom: 20 }}>
              <h2>Change plan</h2>
              <p style={{ color: "var(--muted)" }}>
                Only team admins can change the plan. Ask an admin to make changes.
              </p>
            </section>
          )}
        </>
      )}
    </AppShell>
  );
}

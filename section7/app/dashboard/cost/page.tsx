"use client";

// M10-A3 — /dashboard/cost: live spend dashboard over Section 3's
// GET /api/v1/models/spend?teamId=&sinceDays= (wave 9 cost ledger).
// Range selector drives sinceDays; totals cards, per-model CSS bar list,
// per-day SVG sparkline + table. No chart libraries — pure CSS/SVG. Honest
// empty state and a 503 "metering not wired" notice instead of a crash.

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { loadSession } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import {
  SpendSummary,
  fetchSpend,
  formatTokens,
  formatUsd,
  spendIsEmpty,
} from "./costApi";

const RANGES = [7, 30, 90] as const;
type RangeDays = (typeof RANGES)[number];

const cardStyle = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: 14,
} as const;

const cardTitleStyle = {
  fontSize: 11,
  letterSpacing: 1,
  textTransform: "uppercase",
  color: "var(--muted)",
  margin: "0 0 10px",
} as const;

const thTdStyle = {
  textAlign: "left",
  padding: "4px 8px",
  borderBottom: "1px solid var(--border)",
} as const;

export default function CostDashboardPage() {
  const [rangeDays, setRangeDays] = useState<RangeDays>(30);
  const [summary, setSummary] = useState<SpendSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState<string | null>(null);
  // Distinguishes the endpoint's honest 503 LEDGER_UNAVAILABLE ("cost metering
  // is not wired in this deployment") from any other failure.
  const [unwired, setUnwired] = useState(false);

  const load = useCallback(async (days: RangeDays) => {
    const session = loadSession();
    if (!session) {
      setUnwired(false);
      setText("Not signed in — log in first.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setText(null);
    setUnwired(false);
    try {
      const data = await fetchSpend(
        { teamId: session.principal.teamId, sinceDays: days },
        session.token,
      );
      setSummary(data);
    } catch (e) {
      setSummary(null);
      if (e instanceof ApiError && e.status === 503) {
        setUnwired(true); // explanatory notice below; not an error banner
        setText(null);
      } else {
        setText(String((e as Error)?.message ?? e));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(rangeDays);
  }, [load, rangeDays]);

  const maxModelCost = useMemo(() => {
    if (!summary || summary.byModel.length === 0) return 0;
    return Math.max(...summary.byModel.map((m) => m.costUsd), 0);
  }, [summary]);

  const sparkline = useMemo(() => {
    // Pure-SVG polyline over the byDay series; x/y are fractions of the
    // viewBox so it scales with its container. A zero-height series renders
    // as a flat baseline rather than NaN coordinates.
    if (!summary) return null;
    const pts = summary.byDay;
    if (pts.length === 0) return null;
    const maxCost = Math.max(...pts.map((d) => d.costUsd));
    const w = 100;
    const h = 28;
    const stepX = pts.length > 1 ? w / (pts.length - 1) : 0;
    const points = pts
      .map((d, i) => {
        const x = i * stepX;
        const y = maxCost > 0 ? h - (d.costUsd / maxCost) * (h - 2) - 1 : h - 1;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
    const last = pts[pts.length - 1];
    return { points, viewBox: `0 0 ${w} ${h}`, last };
  }, [summary]);

  const hasData = !unwired && text === null && summary !== null && !spendIsEmpty(summary);

  return (
    <AppShell active="/dashboard/cost">
      <h1>Cost</h1>

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <label htmlFor="cost-range" style={{ color: "var(--muted)" }}>
          Period
        </label>
        <select
          id="cost-range"
          aria-label="Spend period"
          value={rangeDays}
          onChange={(e) => setRangeDays(Number(e.target.value) as RangeDays)}
          style={{
            background: "var(--panel-2)",
            border: "1px solid var(--border)",
            color: "var(--text)",
            borderRadius: 6,
            padding: "6px 8px",
          }}
        >
          {RANGES.map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </select>
        {loading && (
          <span role="status" style={{ color: "var(--muted)" }}>
            Loading…
          </span>
        )}
      </div>

      {/* 503 LEDGER_UNAVAILABLE — cost metering not wired in this deployment.
          Explanatory inline notice, not an error banner, not a crash. */}
      {unwired && (
        <div
          role="status"
          style={{
            border: "1px solid var(--yellow)",
            background: "var(--panel)",
            borderRadius: 8,
            padding: 14,
            marginBottom: 16,
          }}
        >
          <strong style={{ color: "var(--yellow)" }}>Metering not wired.</strong>{" "}
          This deployment has no cost-ledger database connected, so spend cannot be
          measured. Model calls still work — they just run unmetered.
        </div>
      )}

      {text !== null && (
        <p role="alert" style={{ color: "var(--red)" }}>
          {text}
        </p>
      )}

      {!unwired && text === null && summary !== null && spendIsEmpty(summary) && (
        <p style={{ color: "var(--muted)" }}>No spend recorded in this period.</p>
      )}

      {hasData && summary !== null && (
        <>
          <div className="grid">
            <div style={cardStyle}>
              <h2 style={cardTitleStyle}>Total cost</h2>
              <div style={{ fontSize: 22 }}>{formatUsd(summary.totals.costUsd)}</div>
            </div>
            <div style={cardStyle}>
              <h2 style={cardTitleStyle}>Input tokens</h2>
              <div style={{ fontSize: 22 }}>{formatTokens(summary.totals.inputTokens)}</div>
            </div>
            <div style={cardStyle}>
              <h2 style={cardTitleStyle}>Output tokens</h2>
              <div style={{ fontSize: 22 }}>{formatTokens(summary.totals.outputTokens)}</div>
            </div>
          </div>

          <section aria-label="Spend by model" style={{ ...cardStyle, marginTop: 16 }}>
            <h2 style={cardTitleStyle}>By model</h2>
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {summary.byModel.map((m) => {
                // Bar width = % of the most expensive model in the period.
                const pct =
                  maxModelCost > 0
                    ? Math.max(2, Math.round((m.costUsd / maxModelCost) * 100))
                    : 0;
                return (
                  <li key={m.model} style={{ marginBottom: 10 }}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        fontSize: 12,
                        marginBottom: 3,
                      }}
                    >
                      <span
                        title={m.model}
                        style={{
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          maxWidth: "70%",
                        }}
                      >
                        {m.model}
                      </span>
                      <span style={{ color: "var(--muted)", flexShrink: 0 }}>
                        {formatUsd(m.costUsd)}
                      </span>
                    </div>
                    {/* Pure CSS bar — no chart library. */}
                    <div
                      role="img"
                      aria-label={`${m.model}: ${formatUsd(m.costUsd)} (${formatTokens(m.tokens)} tokens)`}
                      style={{
                        height: 6,
                        background: "var(--accent)",
                        width: `${pct}%`,
                        borderRadius: 3,
                      }}
                    />
                  </li>
                );
              })}
            </ul>
          </section>

          <section aria-label="Spend by day" style={{ ...cardStyle, marginTop: 16 }}>
            <h2 style={cardTitleStyle}>By day</h2>
            {sparkline ? (
              <svg
                viewBox={sparkline.viewBox}
                preserveAspectRatio="none"
                role="img"
                aria-label={`Daily spend, latest ${formatUsd(sparkline.last.costUsd)} on ${sparkline.last.day}`}
                style={{ width: "100%", height: 56, display: "block" }}
              >
                <polyline
                  fill="none"
                  stroke="var(--accent)"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                  points={sparkline.points}
                />
              </svg>
            ) : (
              <span style={{ color: "var(--muted)" }}>No daily breakdown available.</span>
            )}
            <table
              style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, marginTop: 10 }}
            >
              <thead>
                <tr>
                  <th style={thTdStyle}>Day</th>
                  <th style={thTdStyle}>Cost</th>
                </tr>
              </thead>
              <tbody>
                {summary.byDay.map((d) => (
                  <tr key={d.day}>
                    <td style={thTdStyle}>{d.day}</td>
                    <td style={thTdStyle}>{formatUsd(d.costUsd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </AppShell>
  );
}

"use client";

// M10-A3 — /dashboard/cost: live spend dashboard over Section 3's
// GET /api/v1/models/spend?teamId=&sinceDays= (wave 9 cost ledger).
// M12-G6 honesty pass: 7/30/90 preset chips drive sinceDays; a metering
// explainer distinguishes zero-because-nothing-ran from zero-because-unwired
// (503 LEDGER_UNAVAILABLE, or any zero we cannot attribute to real activity);
// and the by-day series exports as CSV via a client-side blob download.
// No chart libraries — pure CSS/SVG. No crashes on honest failure modes.

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { loadSession } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import {
  COST_DOCS_URL,
  SpendSummary,
  classifySpendView,
  downloadSpendCsv,
  fetchSpend,
  formatTokens,
  formatUsd,
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

const chipStyle = (active: boolean) =>
  ({
    background: active ? "var(--accent)" : "var(--panel-2)",
    border: "1px solid var(--border)",
    color: active ? "#0b0e14" : "var(--text)",
    borderRadius: 999,
    padding: "5px 12px",
    fontSize: 13,
    cursor: "pointer",
  }) as const;

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

  // H-011: which flavor of "no numbers" are we looking at?
  //  - "unwired": 503 LEDGER_UNAVAILABLE — metering is off; show the explainer.
  //  - "no-recorded-activity": HTTP 200 from a connected ledger with zeroed
  //    totals — activity was genuinely not recorded; page shows a plain
  //    nothing-ran line plus the explainer (recording may bypass some runs).
  //  - "data": non-zero summary — render cards/bars/sparkline/table.
  //  - "pending": first load or fetch error — page renders loading/alert.
  const view = classifySpendView(summary, unwired, text !== null);

  const hasData = view === "data";

  const byDayRows = summary?.byDay ?? [];
  const canExport = !loading && text === null && byDayRows.length > 0;
  const exportDisabledReason =
    loading || text !== null
      ? "Spend data has not loaded yet."
      : "Nothing to export — no daily spend rows recorded in this period.";

  return (
    <AppShell active="/dashboard/cost">
      <h1>Cost</h1>

      {/* M12-G6: 7/30/90 preset chips — each drives fetchSpend's sinceDays. */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <span style={{ color: "var(--muted)" }}>Period</span>
        <div
          role="group"
          aria-label="Spend period"
          data-testid="cost-range-chips"
          style={{ display: "flex", gap: 6 }}
        >
          {RANGES.map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={rangeDays === d}
              data-testid={`cost-range-${d}`}
              onClick={() => setRangeDays(d)}
              style={chipStyle(rangeDays === d)}
            >
              {d}d
            </button>
          ))}
        </div>
        <span aria-live="polite" style={{ color: "var(--muted)", fontSize: 12 }}>
          Last {rangeDays} days
        </span>
        {loading && (
          <span role="status" style={{ color: "var(--muted)" }}>
            Loading…
          </span>
        )}
      </div>

      {/* H-011 explainer — shown whenever the dashboard cannot show truthful
          numbers: the endpoint said LEDGER_UNAVAILABLE (metering not wired),
          or the period came back all zeros (nothing recorded — which may mean
          metering never reached agent runs in the first place). */}
      {(view === "unwired" || view === "no-recorded-activity") && (
        <div
          role="status"
          data-testid="metering-explainer"
          style={{
            border: "1px solid var(--yellow)",
            background: "var(--panel)",
            borderRadius: 8,
            padding: 14,
            marginBottom: 16,
            lineHeight: 1.6,
          }}
        >
          <strong style={{ color: "var(--yellow)" }}>Metering isn&apos;t connected to agent runs yet</strong>{" "}
          — costs will appear here once wired.
          <br />
          <span style={{ color: "var(--muted)", fontSize: 13 }}>
            {view === "unwired"
              ? "This deployment has no cost-ledger database connected, so spend cannot be measured. Model calls still work — they just run unmetered."
              : "No usage was recorded for this period yet. Model calls work, but nothing has been metered — spend rows appear here after the first metered run."}
          </span>
          <br />
          <a href={COST_DOCS_URL} target="_blank" rel="noreferrer" style={{ color: "#93c5fd" }}>
            Status &amp; docs reference ↗
          </a>
        </div>
      )}

      {text !== null && (
        <p role="alert" style={{ color: "var(--red)" }}>
          {text}
        </p>
      )}

      {/* Wired ledger, genuinely nothing recorded in this period — keep the
          plain line too so both truths are visible at once. */}
      {view === "no-recorded-activity" && (
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
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
              }}
            >
              <h2 style={{ ...cardTitleStyle, marginBottom: 0 }}>By day</h2>
              {/* M12-G6: client-side CSV export of the byDay series. Disabled
                  (with the reason on the tooltip) while there is nothing to
                  export — never downloads an empty file. */}
              <span title={canExport ? undefined : exportDisabledReason}>
                <button
                  type="button"
                  data-testid="export-csv"
                  disabled={!canExport}
                  onClick={() => downloadSpendCsv(byDayRows)}
                  style={{
                    ...chipStyle(false),
                    cursor: canExport ? "pointer" : "not-allowed",
                    opacity: canExport ? 1 : 0.55,
                  }}
                >
                  Export CSV
                </button>
              </span>
            </div>
            {!canExport && (
              <p style={{ color: "var(--muted)", fontSize: 12, margin: "6px 0 0" }}>
                {exportDisabledReason}
              </p>
            )}
            {sparkline ? (
              <svg
                viewBox={sparkline.viewBox}
                preserveAspectRatio="none"
                role="img"
                aria-label={`Daily spend, latest ${formatUsd(sparkline.last.costUsd)} on ${sparkline.last.day}`}
                style={{ width: "100%", height: 56, display: "block", marginTop: 10 }}
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

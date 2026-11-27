// X-21 Decision-card severity grid. Rows = access families, columns = low -> critical. Cells come
// from the server (section4 approval_copy.py, a fixed policy table) — this component never infers
// risk. Every active cell carries icon + text, never colour alone; the dominant band is a sentence
// read before the Approve/Deny buttons (it sits above them in the card's DOM/focus order).
import * as React from "react";
import { Pill } from "@/components/ui";
import type { StatusTone } from "@/components/tokens";

export type Band = "low" | "moderate" | "elevated" | "critical";
export interface DecisionGrid {
  cells: { family: string; band: Band; label: string }[];
  dominantBand: Band | "unknown";
}

const BANDS: Band[] = ["low", "moderate", "elevated", "critical"];
const TONE: Record<Band, StatusTone> = { low: "green", moderate: "blue", elevated: "yellow", critical: "red" };
const ICON: Record<Band, string> = { low: "○", moderate: "◐", elevated: "▲", critical: "■" };
// Fits a 375 px phone card: fixed layout, compact cells, plain-case headers (the global th style
// uppercases and letter-spaces, which pushed the elevated/critical columns out of the card).
const HEAD = { textTransform: "none", letterSpacing: "normal", fontSize: 11, padding: "2px 1px" } as const;
const CELL = { padding: "1px 3px", fontSize: 11, whiteSpace: "nowrap" } as const;
// Short visible labels so four columns fit a phone; the full word stays in the accessible name.
const SHORT: Record<Band, string> = { low: "low", moderate: "mod", elevated: "elev", critical: "crit" };
const Label = ({ b }: { b: Band }) => (
  <><span aria-hidden="true" title={b}>{SHORT[b]}</span><span className="sr-only">{b}</span></>
);

export function DecisionCardGrid({ grid, testId }: { grid: DecisionGrid; testId?: string }) {
  const d = grid.dominantBand;
  return (
    <div data-testid={testId}>
      <p style={{ margin: "4px 0", fontWeight: 600 }} data-testid={testId && `${testId}-dominant`}>
        Overall risk: {d === "unknown" ? "unknown (could not classify)" : <Pill tone={TONE[d]}><span aria-hidden="true">{ICON[d]} </span>{d}</Pill>}
      </p>
      {grid.cells.length > 0 && (
        <table style={{ borderCollapse: "collapse", fontSize: 12, width: "100%", maxWidth: 520, tableLayout: "fixed" }}>
          <caption style={{ textAlign: "left", color: "var(--muted)" }}>What this request touches, by severity</caption>
          <thead>
            <tr><th scope="col" style={{ ...HEAD, textAlign: "left", width: "30%" }}>Access</th>{BANDS.map((b) => <th key={b} scope="col" style={{ ...HEAD, textAlign: "center" }}><Label b={b} /></th>)}</tr>
          </thead>
          <tbody>
            {grid.cells.map((c) => (
              <tr key={c.family} data-family={c.family}>
                <th scope="row" style={{ ...HEAD, textAlign: "left", fontWeight: 400 }}>{c.label}</th>
                {BANDS.map((b) => (
                  <td key={b} style={{ textAlign: "center", padding: 1 }}>
                    {b === c.band ? <Pill tone={TONE[b]} style={CELL} testId="grid-cell"><span aria-hidden="true">{ICON[b]} </span><Label b={b} /></Pill> : null}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

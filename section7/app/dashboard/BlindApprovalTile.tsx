"use client";
// X-07 standing KPI tile: blind-approval rate by rendering (section8 GET /api/v1/review-kpi/
// blind-approval, admins only). "Blind" = approved while the diff was never on screen. Below
// the minimum sample the tile says "insufficient data" instead of a number; approvals from
// clients that did not measure are shown as unknown, never counted as blind. Counts only.
import * as React from "react";
import { useEffect, useState } from "react";
import { Card } from "@/components/ui";
import { REVIEW_BASE, apiFetch } from "@/lib/api";
import { loadSession } from "@/lib/auth";

interface Bucket { decisions: number; approvals: number; withSignal: number; blind: number; unknownSignal: number; rate: number | null }
export interface BlindApprovalKpi { window: number; minSample: number; overall: Bucket; byRendering: Record<string, Bucket> }

export const getBlindApprovalKpi = (token: string) =>
  apiFetch<BlindApprovalKpi>(`${REVIEW_BASE}/api/v1/review-kpi/blind-approval`, token);

const pct = (b: Bucket, min: number) =>
  b.rate === null ? `insufficient data (${b.withSignal} of ${min} measured approvals)` : `${Math.round(b.rate * 100)}%`;

export function BlindApprovalView({ kpi }: { kpi: BlindApprovalKpi }) {
  return (
    <Card>
      <h2 style={{ fontSize: 15 }}>Blind-approval rate</h2>
      <p data-testid="kpi-overall">Overall: <strong>{pct(kpi.overall, kpi.minSample)}</strong></p>
      <table style={{ fontSize: 13 }}>
        <caption className="sr-only">Blind-approval rate by approval rendering</caption>
        <thead><tr><th scope="col">Rendering</th><th scope="col">Rate</th><th scope="col">Approvals</th><th scope="col">Not measured</th></tr></thead>
        <tbody>
          {Object.entries(kpi.byRendering).map(([r, b]) => (
            <tr key={r} data-testid={`kpi-${r}`}><th scope="row">{r}</th><td>{pct(b, kpi.minSample)}</td><td>{b.approvals}</td><td>{b.unknownSignal}</td></tr>
          ))}
        </tbody>
      </table>
      <p style={{ color: "var(--muted)", fontSize: 12 }}>Last {kpi.window} review decisions. Blind means approved without the diff ever being on screen. Trend: not tracked yet.</p>
    </Card>
  );
}

export function BlindApprovalTile() {
  const [kpi, setKpi] = useState<BlindApprovalKpi | null>(null);
  const [admin, setAdmin] = useState(false);
  useEffect(() => {
    const s = loadSession();
    if (!s || s.principal.role !== "admin") return;
    setAdmin(true);
    getBlindApprovalKpi(s.token).then(setKpi).catch(() => setKpi(null));
  }, []);
  if (!admin) return null;
  return kpi ? <BlindApprovalView kpi={kpi} /> : <p style={{ color: "var(--muted)" }}>Blind-approval rate unavailable.</p>;
}

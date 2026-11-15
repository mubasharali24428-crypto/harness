// R5 pure rule: the run's recorded events as a waterfall. Offsets and widths
// come from the events' own timestamps; a step's duration is the server's
// durationMs when it sent one, otherwise the gap to the next recorded event.

import { stepLabel, type RunStepDto } from "../../../app/runs/runsApi";

export interface TraceRow {
  seq: number;
  label: string;
  /** Milliseconds after the first recorded event. */
  offsetMs: number;
  durationMs: number;
  /** True when the server measured the duration; false when it is the gap to the next event. */
  measured: boolean;
  /** Bar position and width as a share of the whole trace, 0..100. */
  leftPct: number;
  widthPct: number;
}

export interface Trace {
  rows: TraceRow[];
  totalMs: number;
  /** Events left out because the server recorded no time for them. */
  untimed: number;
}

export function buildTrace(steps: readonly RunStepDto[]): Trace {
  const timed = steps
    .map((s) => ({ s, t: s.ts ? Date.parse(s.ts) : NaN }))
    .filter((x) => Number.isFinite(x.t))
    .sort((a, b) => a.t - b.t || a.s.seq - b.s.seq);
  const untimed = steps.length - timed.length;
  if (timed.length === 0) return { rows: [], totalMs: 0, untimed };
  const start = timed[0].t;
  const raw = timed.map((x, i) => {
    const own = x.s.detail?.durationMs;
    const measured = typeof own === "number" && Number.isFinite(own) && own >= 0;
    const gap = i + 1 < timed.length ? timed[i + 1].t - x.t : 0;
    return { x, offsetMs: x.t - start, durationMs: measured ? (own as number) : gap, measured };
  });
  const totalMs = Math.max(...raw.map((r) => r.offsetMs + r.durationMs));
  const pct = (ms: number) => (totalMs > 0 ? (ms / totalMs) * 100 : 0);
  return {
    totalMs,
    untimed,
    rows: raw.map((r) => ({
      seq: r.x.s.seq,
      label: stepLabel(r.x.s.type, r.x.s.reason),
      offsetMs: r.offsetMs,
      durationMs: r.durationMs,
      measured: r.measured,
      leftPct: Math.min(pct(r.offsetMs), 99),
      widthPct: Math.max(pct(r.durationMs), 1),
    })),
  };
}

/** "850 ms", "12.4 s", "3 min 05 s". */
export function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}

// M13-G5b — shared client-side formatters. ONE date-fns-free module so every
// page renders times the same way (the runs table previously owned its own
// relative-time helper while settings/checkpoints used raw toLocaleString()).
// Pure functions only — no DOM, no fetch — so vitest covers them directly.

/** Pure: relative time ("4m ago" / "in 3h") from an ISO timestamp against
 *  `now` (epoch ms). Null/unparseable input renders as "—" — never a
 *  fabricated time. Future timestamps read as "in X"; past ones as "X ago". */
export function formatRelativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "—";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "—";
  const diffMs = now - then;
  const seconds = Math.floor(Math.abs(diffMs) / 1000);
  let span: string;
  if (seconds < 5) return "just now";
  if (seconds < 60) span = `${seconds}s`;
  else if (seconds < 3600) span = `${Math.floor(seconds / 60)}m`;
  else if (seconds < 86400) span = `${Math.floor(seconds / 3600)}h`;
  else span = `${Math.floor(seconds / 86400)}d`;
  return diffMs < 0 ? `in ${span}` : `${span} ago`;
}

/** Pure: absolute local timestamp for titles/tooltips and detail views.
 *  Unparseable input falls back to the raw string — never a fabricated date. */
export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Pure: filesystem/URL-safe lowercase slug of a team name, for export
 *  filenames ("Acme Robotics!" → "acme-robotics"). Empty/blank names collapse
 *  to "" so callers can fall back to an unnamed-team marker. */
export function teamNameSlug(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug.slice(0, 40);
}

/**
 * R1 money rule (ui-master-prompt §Visual rules): two decimals from one cent
 * up, "< $0.01" below a cent, "unknown" when not recorded. A genuine $0 IS
 * recorded data and renders as "$0.00"; null/NaN/∞ never render as money.
 * Pure so vitest covers it directly.
 */
export function formatMoneyUsd(
  value: number | null | undefined,
): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return "unknown";
  }
  if (value > 0 && value < 0.01) return "< $0.01";
  return `$${value.toFixed(2)}`;
}

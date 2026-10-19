// Part 56: locale-aware dates and numbers for the collaboration, discussion and notification UI. Every
// formatter takes an optional locale (default: the browser's) and an optional IANA time zone, so the
// output follows the person's settings and tests can pin both. Unparseable input renders as an em dash,
// never a fabricated date (same rule as lib/format.ts).

const cache = new Map<string, Intl.DateTimeFormat | Intl.NumberFormat | Intl.RelativeTimeFormat>();
function memo<T extends Intl.DateTimeFormat | Intl.NumberFormat | Intl.RelativeTimeFormat>(key: string, make: () => T): T {
  let f = cache.get(key) as T | undefined;
  if (!f) { f = make(); cache.set(key, f); }
  return f;
}

export interface FormatOpts { locale?: string; timeZone?: string }

const parse = (iso: string | null | undefined): Date | null => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
};

function dt(iso: string | null | undefined, style: Intl.DateTimeFormatOptions, o: FormatOpts): string {
  const d = parse(iso);
  if (!d) return "—";
  const key = `dt|${o.locale ?? ""}|${o.timeZone ?? ""}|${JSON.stringify(style)}`;
  try {
    return memo(key, () => new Intl.DateTimeFormat(o.locale, { ...style, timeZone: o.timeZone })).format(d);
  } catch { return d.toISOString(); } // unknown locale or zone: still an honest value
}

export const formatDateTime = (iso: string | null | undefined, o: FormatOpts = {}) => dt(iso, { dateStyle: "medium", timeStyle: "short" }, o);
export const formatTime = (iso: string | null | undefined, o: FormatOpts = {}) => dt(iso, { timeStyle: "medium" }, o);
export const formatDate = (iso: string | null | undefined, o: FormatOpts = {}) => dt(iso, { dateStyle: "medium" }, o);

export function formatNumber(n: number, o: { locale?: string } = {}): string {
  return memo(`n|${o.locale ?? ""}`, () => new Intl.NumberFormat(o.locale)).format(n);
}

/** "in 3 hours" / "5 minutes ago" in the person's language; `now` is epoch ms. */
export function formatRelative(iso: string | null | undefined, now: number = Date.now(), o: { locale?: string } = {}): string {
  const d = parse(iso);
  if (!d) return "—";
  const diffSec = Math.round((d.getTime() - now) / 1000);
  const abs = Math.abs(diffSec);
  const [unit, size]: [Intl.RelativeTimeFormatUnit, number] =
    abs < 60 ? ["second", 1] : abs < 3600 ? ["minute", 60] : abs < 86400 ? ["hour", 3600] : ["day", 86400];
  return memo(`rt|${o.locale ?? ""}`, () => new Intl.RelativeTimeFormat(o.locale, { numeric: "auto" })).format(Math.round(diffSec / size), unit);
}

/** The zone names the browser knows, for the quiet-hours picker; a short fallback where unsupported. */
export function timeZones(): string[] {
  const f = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
  if (typeof f === "function") { try { return ["UTC", ...f("timeZone").filter((z) => z !== "UTC")]; } catch { /* fall through */ } }
  return ["UTC", "Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles", "Asia/Karachi", "Asia/Tokyo", "Australia/Sydney"];
}

/** The person's own zone, or UTC when the browser will not say. */
export function browserTimeZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}

/** "8 AM" / "08:00" for an hour of the day, in the person's locale. */
export function hourLabel(hour: number, o: { locale?: string } = {}): string {
  const d = new Date(Date.UTC(2000, 0, 1, hour));
  return memo(`h|${o.locale ?? ""}`, () => new Intl.DateTimeFormat(o.locale, { hour: "numeric", timeZone: "UTC" })).format(d);
}

// A4-S5 (F138): message catalogs, RTL direction and timezone-correct time formatting.
// English is the source of truth; other catalogs must match its keys and {placeholders}
// (enforced by tests), and a missing key falls back to English rather than showing a key.
export type Locale = "en" | "es" | "ar";
export const LOCALES: readonly Locale[] = ["en", "es", "ar"];

export const EN = {
  "inbox.title": "Task inbox",
  "inbox.waiting": "{n} waiting for a decision",
  "inbox.noRun": "Approving records your decision. It does not start a run.",
  "inbox.empty": "No queued tasks.",
  "inbox.approve": "Approve",
  "inbox.reject": "Reject",
  "inbox.approveLabel": "Approve {title}",
  "inbox.rejectLabel": "Reject {title}",
  "inbox.open": "Open original",
  "inbox.readOnly": "Your role can view the inbox but not decide.",
  "inbox.approved": "Approved and recorded. No run was started.",
  "inbox.rejected": "Rejected.",
  "palette.title": "Command palette",
  "palette.placeholder": "Type a command",
  "palette.none": "No matching commands",
  "status.title": "Service status",
  "status.operational": "Operational",
  "status.degraded": "Degraded",
  "status.notConfigured": "Not configured",
  "status.unreachable": "Unreachable",
} as const;
export type Key = keyof typeof EN;

const ES: Record<Key, string> = {
  "inbox.title": "Bandeja de tareas",
  "inbox.waiting": "{n} esperando una decisión",
  "inbox.noRun": "Aprobar registra tu decisión. No inicia ninguna ejecución.",
  "inbox.empty": "No hay tareas en cola.",
  "inbox.approve": "Aprobar",
  "inbox.reject": "Rechazar",
  "inbox.approveLabel": "Aprobar {title}",
  "inbox.rejectLabel": "Rechazar {title}",
  "inbox.open": "Abrir original",
  "inbox.readOnly": "Tu rol puede ver la bandeja pero no decidir.",
  "inbox.approved": "Aprobado y registrado. No se inició ninguna ejecución.",
  "inbox.rejected": "Rechazado.",
  "palette.title": "Paleta de comandos",
  "palette.placeholder": "Escribe un comando",
  "palette.none": "Ningún comando coincide",
  "status.title": "Estado del servicio",
  "status.operational": "Operativo",
  "status.degraded": "Degradado",
  "status.notConfigured": "No configurado",
  "status.unreachable": "Inaccesible",
};
const AR: Record<Key, string> = {
  "inbox.title": "صندوق المهام",
  "inbox.waiting": "{n} بانتظار القرار",
  "inbox.noRun": "الموافقة تسجّل قرارك فقط. ولا تبدأ أي تشغيل.",
  "inbox.empty": "لا توجد مهام في الانتظار.",
  "inbox.approve": "موافقة",
  "inbox.reject": "رفض",
  "inbox.approveLabel": "الموافقة على {title}",
  "inbox.rejectLabel": "رفض {title}",
  "inbox.open": "فتح الأصل",
  "inbox.readOnly": "دورك يتيح عرض الصندوق دون اتخاذ قرار.",
  "inbox.approved": "تمت الموافقة والتسجيل. لم يبدأ أي تشغيل.",
  "inbox.rejected": "تم الرفض.",
  "palette.title": "لوحة الأوامر",
  "palette.placeholder": "اكتب أمرًا",
  "palette.none": "لا توجد أوامر مطابقة",
  "status.title": "حالة الخدمة",
  "status.operational": "تعمل",
  "status.degraded": "متدهورة",
  "status.notConfigured": "غير مهيأة",
  "status.unreachable": "غير متاحة",
};
export const CATALOGS: Record<Locale, Record<Key, string>> = { en: EN, es: ES, ar: AR };

export type Translate = (key: Key, vars?: Record<string, string | number>) => string;

export function makeT(locale: Locale): Translate {
  const cat = CATALOGS[locale] ?? EN;
  return (key, vars = {}) =>
    (cat[key] ?? EN[key]).replace(/\{(\w+)\}/g, (_, k: string) => (k in vars ? String(vars[k]) : `{${k}}`));
}

export const dirFor = (l: Locale): "ltr" | "rtl" => (l === "ar" ? "rtl" : "ltr");

export function parseLocale(raw: string | null | undefined): Locale {
  const base = (raw ?? "").toLowerCase().split("-")[0];
  return (LOCALES as readonly string[]).includes(base) ? (base as Locale) : "en";
}

export function isValidTimeZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** Absolute time in an explicit IANA zone, so two users in different zones see the same
 *  instant described correctly. An invalid zone falls back to UTC (never to a guess), and
 *  unparseable input is returned raw rather than fabricated. */
export function formatDateTime(iso: string | null, locale: Locale, timeZone: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const tz = isValidTimeZone(timeZone) ? timeZone : "UTC";
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: tz, timeZoneName: "short" }).format(d);
}

export function formatRelative(iso: string | null, locale: Locale, now = Date.now()): string {
  if (!iso) return "—";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "—";
  const s = Math.round((then - now) / 1000);
  const fmt = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const abs = Math.abs(s);
  if (abs < 60) return fmt.format(s, "second");
  if (abs < 3600) return fmt.format(Math.round(s / 60), "minute");
  if (abs < 86400) return fmt.format(Math.round(s / 3600), "hour");
  return fmt.format(Math.round(s / 86400), "day");
}

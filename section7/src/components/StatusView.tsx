// A4-S5 (F139): service status. Text + icon-free states so colour is never the only signal.
import * as React from "react";
import { makeT, type Translate } from "@/lib/i18n";

export interface ComponentStatus { name: string; state: "operational" | "degraded" | "not_configured" | "unreachable"; detail?: string }
const KEY = { operational: "status.operational", degraded: "status.degraded", not_configured: "status.notConfigured", unreachable: "status.unreachable" } as const;

export function StatusView({ components, checkedAt, t = makeT("en") }: { components: ComponentStatus[]; checkedAt: string; t?: Translate }) {
  return (
    <section aria-labelledby="status-title" data-testid="status">
      <h2 id="status-title">{t("status.title")}</h2>
      <ul>
        {components.map((c) => (
          <li key={c.name} data-state={c.state}><strong>{c.name}</strong>: {t(KEY[c.state])}{c.detail ? ` — ${c.detail}` : ""}</li>
        ))}
      </ul>
      <p><time dateTime={checkedAt}>{checkedAt}</time></p>
    </section>
  );
}

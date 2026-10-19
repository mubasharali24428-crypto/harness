// Part 46: the visible connection indicator. Words, not colour; deliberately NOT a live region (changes
// reach screen readers through the throttled announcer, so a flapping link cannot flood them).
import * as React from "react";
import { Button } from "@/components/ui";
import { type ConnStatus, statusText } from "./realtimeCore";

const muted = { color: "var(--muted)" } as const;

export function ConnectionStatus({ status, updateRequired, onReload }: {
  status: ConnStatus; updateRequired: boolean; onReload?: () => void;
}) {
  const bad = status === "access_lost" || updateRequired;
  return (
    <p data-testid="connection-status" data-status={updateRequired ? "update_required" : status} style={{ margin: "0 0 8px", fontSize: 13, ...(bad ? {} : muted) }}>
      <strong>Connection:</strong> {statusText(status, updateRequired)}{" "}
      {updateRequired && onReload && <Button size="sm" onClick={onReload}>Reload page</Button>}
    </p>
  );
}

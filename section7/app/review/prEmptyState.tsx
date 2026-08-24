// M12-E4b (H-001) — honest empty state for the review area. Replaces the old
// dead-end copy ("set harness.prId", "open a PR from an approved run") that
// implied hand-editing localStorage. Kept in its own module (no AppShell /
// next-navigation imports) so node-env vitest can render it via
// react-dom/server, same pattern as app/dashboard/planGate.test.tsx.
import * as React from "react";

/** The one honest sentence. Exported so tests assert on the EXACT copy. */
export const PR_PENDING_COPY =
  "No pull request yet — it appears here the moment the agent opens one";

export function PrEmptyState() {
  return (
    <div className="card">
      <h2>Nothing to review yet</h2>
      <p>{PR_PENDING_COPY}.</p>
    </div>
  );
}

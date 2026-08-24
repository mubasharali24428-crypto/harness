// M12-E4b (H-001 UI half + W2-C2 consumer side) — pure prId acquisition for
// the review page. Three paths, in precedence order:
//   1. Deep link  /review?pr=<prId>          (prIdFromSearch)
//   2. Bus events pr_opened {runId,prId,url} (prIdFromEvent / adoptEventPrId)
//   3. Neither produced one → caller renders the honest empty state.
// No hand-editing localStorage anywhere. The pr_opened PRODUCER is lane E4a
// (runs in parallel) — this file only codes against the W2-C2 payload shape;
// producer-side behavior is UNVERIFIED here pending ATLAS merge coordination.
import { EventType } from "@contracts/events";
import type { EventEnvelope } from "@contracts/events";

/** Non-empty after trimming → the trimmed value; anything else → null. */
function asNonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/** Path 1 — deep link. Accepts a location.search string ("?pr=12" or "pr=12").
 *  Returns the trimmed prId, or null when absent/blank. */
export function prIdFromSearch(search: string): string | null {
  try {
    const query = search.startsWith("?") ? search.slice(1) : search;
    return asNonEmptyString(new URLSearchParams(query).get("pr"));
  } catch {
    return null;
  }
}

/** Path 2 — one bus envelope. Matches ONLY when the pr_opened event belongs to
 *  the run under review: payload.runId per W2-C2, falling back to the
 *  envelope-level runId (Section 1 delivery field). With no current run there
 *  is nothing to attribute the PR to, so nothing is adopted. */
export function prIdFromEvent(
  envelope: EventEnvelope,
  currentRunId: string | null,
): string | null {
  if (envelope.type !== EventType.PR_OPENED) return null;
  if (!currentRunId) return null;
  const payload = envelope.payload ?? {};
  const eventRunId =
    asNonEmptyString(payload.runId) ?? asNonEmptyString(envelope.runId);
  if (eventRunId !== currentRunId) return null;
  return asNonEmptyString(payload.prId);
}

/** Path 2 across the whole stream: the newest matching pr_opened wins.
 *  Returns the prId to adopt, or null when none applies. Precedence rule:
 *  once a prId is already held (deep link or remembered session), replayed
 *  history from the bus MUST NOT clobber it — the socket replays from seq 0
 *  on every mount, so unconditional adoption would flip a deliberate deep
 *  link to whichever PR happens to be newest. */
export function adoptEventPrId(
  currentPrId: string | null,
  events: readonly EventEnvelope[],
  currentRunId: string | null,
): string | null {
  if (currentPrId) return null;
  for (let i = events.length - 1; i >= 0; i--) {
    const found = prIdFromEvent(events[i], currentRunId);
    if (found) return found;
  }
  return null;
}

/** Combined initial resolution on mount: deep link first, then the value the
 *  session already remembers (written by THIS page via setPrId — never by
 *  hand). Null means neither source produced anything → honest empty state. */
export function resolvePrId(search: string, stored: string | null): string | null {
  return prIdFromSearch(search) ?? asNonEmptyString(stored);
}

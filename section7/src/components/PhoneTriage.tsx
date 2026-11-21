"use client";

// U5 phone triage (F6): a phone-width inbox that lists ONLY pending approvals,
// with Approve and Deny as the only actions. No working-now column, no run
// links, no start, no filters — a person decides or leaves the screen.
//
// Data and wire calls reuse the EXISTING desktop paths:
//   • list  — listRuns({ limit: 100 }, token) + triageRuns (src/lib/triage.ts)
//   • approve/deny — approvePlan / rejectPlan (app/dashboard/planGateApi.ts),
//     the same pair the desktop PlanGateCard uses.
// Styling follows src/components/ui (tokens, Button, Card, States). No route
// is wired here; the reviewer wires it after this lands.
import { useCallback, useEffect, useMemo, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for .tsx.
import * as React from "react";
import { getToken } from "@/lib/auth";
import { formatRelativeTime, formatTimestamp } from "@/lib/format";
import { triageRuns } from "@/lib/triage";
import { listRuns, shortId, type RunDto } from "../../app/runs/runsApi";
import {
  approvePlan,
  rejectPlan,
} from "../../app/dashboard/planGateApi";
import { Button } from "./ui";
import { EmptyState, ErrorState, LoadingState } from "./States";
import { cssVar, fontSize, radius, space } from "./tokens";

const REFRESH_MS = 15_000;

/** The one pending kind this screen acts on. Everything else is out of scope. */
export type PhonePendingKind = "plan-approval" | "plan-start";

export interface PhonePendingApproval {
  run: RunDto;
  kind: PhonePendingKind;
  /** planId to send to approve/deny; null when the list did not carry one. */
  planId: string | null;
  goal: string | null;
}

export interface PhoneTriageStrings {
  title: string;
  emptyTitle: string;
  emptyBody: string;
  loading: string;
  approve: string;
  deny: string;
  startLabel: string;
}

export const PHONE_TRIAGE_STRINGS: PhoneTriageStrings = {
  title: "Needs you",
  emptyTitle: "Nothing is waiting on you",
  emptyBody: "No plan is pending approval. Working runs stay on the desktop inbox.",
  loading: "Loading pending approvals…",
  approve: "Approve",
  deny: "Deny",
  startLabel: "Start the approved run",
};

/** Pure: which list items are actionable approvals (U5: approve or deny only).
 *  An already-approved plan still shows, as Start, because the desktop inbox
 *  counts it as needs-you; it is not a second Approve. */
export function pendingApprovals(runs: RunDto[]): PhonePendingApproval[] {
  return triageRuns(runs).needsYou
    .filter((r) => r.status === "awaiting_plan_approval")
    .map((r) => ({
      run: r,
      kind: r.planApproved ? "plan-start" : "plan-approval",
      planId: r.planId ?? null,
      goal: r.goal ?? null,
    }));
}

/** Pure: the two decisions this screen may take. Start is not one of them. */
export function actionsForItem(
  kind: PhonePendingKind,
  planId: string | null,
  busy: boolean,
): { approve: boolean; deny: boolean } {
  if (kind !== "plan-approval" || !planId || busy) return { approve: false, deny: false };
  return { approve: true, deny: true };
}

/** The one wire path a decision takes: the SAME desktop plan-gate clients.
 *  Exported so tests can pin URL/method/body without a DOM click. */
export async function decidePlan(
  item: PhonePendingApproval,
  decision: "approve" | "deny",
  token: string,
): Promise<void> {
  if (!item.planId) throw new Error("This pending item has no plan id to act on.");
  if (decision === "approve") await approvePlan(item.planId, token);
  else await rejectPlan(item.planId, token);
}

/** Pure: short honest caption under each kind. */
export function itemCaption(kind: PhonePendingKind): string {
  return kind === "plan-approval"
    ? "Plan awaiting your decision."
    : "Plan approved — start it from the run page.";
}

const listStyle: React.CSSProperties = {
  display: "grid",
  gap: space.md,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const itemStyle: React.CSSProperties = {
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.lg,
  padding: space.cardPad,
  background: cssVar("panel2"),
  display: "grid",
  gap: space.sm,
};

const goalStyle: React.CSSProperties = {
  margin: 0,
  color: cssVar("text"),
  fontSize: fontSize.md,
  lineHeight: 1.4,
  overflowWrap: "anywhere",
};

const metaStyle: React.CSSProperties = {
  margin: 0,
  color: cssVar("muted"),
  fontSize: fontSize.sm,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  gap: space.md,
  flexWrap: "wrap",
};

/** Connected phone inbox. `token` is optional for tests; production callers
 *  omit it and the session token is read through the existing getToken(). */
export function PhoneTriage({
  token: tokenProp,
  strings = PHONE_TRIAGE_STRINGS,
  now = Date.now(),
  onDecided,
  items: itemsProp,
}: {
  token?: string | null;
  strings?: PhoneTriageStrings;
  now?: number;
  onDecided?: (item: PhonePendingApproval, decision: "approve" | "deny") => void;
  /** Prebuilt pending list (tests / a parent that already has the page).
   *  Omit in production: the component then loads listRuns itself. */
  items?: PhonePendingApproval[] | null;
}) {
  const resolvedToken = tokenProp !== undefined ? tokenProp : getToken();
  const [runs, setRuns] = useState<RunDto[] | null>(itemsProp ? [] : null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [itemError, setItemError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [decidedIds, setDecidedIds] = useState<Set<string>>(() => new Set());

  const load = useCallback(async () => {
    if (!resolvedToken) return;
    try {
      const page = await listRuns({ limit: 100 }, resolvedToken);
      setRuns(page.items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [resolvedToken]);

  useEffect(() => {
    if (!resolvedToken) return;
    let alive = true;
    void load();
    const id = window.setInterval(() => {
      if (alive) void load();
    }, REFRESH_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [resolvedToken, load, itemsProp]);

  const items = useMemo(
    () => (itemsProp ? itemsProp : runs ? pendingApprovals(runs) : null),
    [itemsProp, runs],
  );

  const decide = async (item: PhonePendingApproval, decision: "approve" | "deny") => {
    if (!item.planId || !resolvedToken || busyId) return;
    setBusyId(item.run.id);
    setItemError(null);
    setNote(null);
    try {
      await decidePlan(item, decision, resolvedToken);
      setDecidedIds((prev) => new Set(prev).add(item.run.id));
      setNote(decision === "approve" ? "Plan approved." : "Plan denied.");
      onDecided?.(item, decision);
      await load();
    } catch (err) {
      setItemError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  if (!resolvedToken) {
    return (
      <section
        aria-labelledby="phone-triage-h"
        data-testid="phone-triage"
        style={{ width: "100%", maxWidth: 480, margin: "0 auto" }}
      >
        <h2 id="phone-triage-h">{strings.title}</h2>
        <EmptyState title={strings.emptyTitle} body="Sign in to see pending approvals." />
      </section>
    );
  }

  return (
    <section
      aria-labelledby="phone-triage-h"
      data-testid="phone-triage"
      style={{ width: "100%", maxWidth: 480, margin: "0 auto" }}
    >
      <h2
        id="phone-triage-h"
        style={{
          margin: `0 0 ${space.lg}px`,
          fontSize: fontSize.md,
          color: cssVar("text"),
        }}
      >
        {strings.title}
        {items && items.length > 0 ? (
          <span
            style={{
              marginLeft: space.sm,
              color: cssVar("muted"),
              fontSize: fontSize.sm,
            }}
          >
            {items.length}
          </span>
        ) : null}
      </h2>

      {error ? (
        <ErrorState title="Pending approvals didn’t load" message={error} onRetry={() => void load()} />
      ) : !items ? (
        <LoadingState label={strings.loading} rows={2} />
      ) : items.length === 0 ? (
        <EmptyState title={strings.emptyTitle} body={strings.emptyBody} />
      ) : (
        <ol style={listStyle}>
          {items.map((item) => {
            const enabled = actionsForItem(item.kind, item.planId, busyId !== null);
            const decided = decidedIds.has(item.run.id);
            const when = item.run.startedAt ?? item.run.createdAt;
            return (
              <li key={item.run.id} style={itemStyle} data-testid="phone-triage-item">
                <p style={goalStyle}>{item.goal ?? "Plan awaiting your decision."}</p>
                <p style={metaStyle}>
                  run {shortId(item.run.id)} · {formatRelativeTime(when, now)}
                  <time title={formatTimestamp(when)} dateTime={when} />
                </p>
                <p
                  style={{
                    margin: 0,
                    color: cssVar("muted"),
                    fontSize: fontSize.sm,
                  }}
                >
                  {itemCaption(item.kind)}
                </p>
                <div style={rowStyle}>
                  {item.kind === "plan-approval" ? (
                    <>
                      <Button
                        variant="primary"
                        size="md"
                        testId="phone-approve"
                        disabled={!enabled.approve || decided}
                        onClick={() => void decide(item, "approve")}
                      >
                        {strings.approve}
                      </Button>
                      <Button
                        variant="danger"
                        size="md"
                        testId="phone-deny"
                        disabled={!enabled.deny || decided}
                        onClick={() => void decide(item, "deny")}
                      >
                        {strings.deny}
                      </Button>
                    </>
                  ) : (
                    <span style={{ color: cssVar("muted"), fontSize: fontSize.sm }}>
                      {strings.startLabel}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {note ? (
        <p role="status" data-testid="phone-triage-note" style={{ color: cssVar("green"), fontSize: fontSize.sm }}>
          {note}
        </p>
      ) : null}
      {itemError ? (
        <p role="alert" data-testid="phone-triage-error" style={{ color: cssVar("red"), fontSize: fontSize.sm }}>
          {itemError}
        </p>
      ) : null}
    </section>
  );
}

export default PhoneTriage;

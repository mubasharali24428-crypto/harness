"use client";

// M14-F4 (Wave 4, lane F4) — generic optimistic-mutation pattern.
//
//   1. apply()     mutate local state NOW, capturing a rollback snapshot
//   2. request()   fire the real wire call
//   3. confirmed   reconcile with server truth (commit)
//      rolled-back restore the snapshot + surface the error
//
// The ENGINE (runOptimistic) is a framework-free async function so the whole
// success/fail/rollback lifecycle is headless-testable in this repo's node-env
// vitest (no jsdom/RTL available, installs out of scope). The hook is a thin
// useState/ref wrapper around it — same split as planGateApi.ts (pure logic +
// connected shell).
//
// Error copy reuses the M12-E2 friendlyErrorMessage map (src/lib/api.ts):
// known codes get their human sentence; unknown ones surface the SERVER's
// message verbatim (W2-C1 requirement for e.g. LAST_ADMIN) — never a
// fabricated excuse. Single-flight per hook instance is expected (callers
// disable controls while pending); a newer mutate() supersedes the older
// run's status writes so a stale completion can't clobber fresh UI state.

import { useCallback, useRef, useState } from "react";
import { ApiError, friendlyErrorMessage } from "@/lib/api";

/** Lifecycle of one optimistic mutation (observable for status-driven UI). */
export type OptimisticStatus =
  | "idle"
  | "pending"
  | "confirmed"
  | "rolled-back"
  | "failed"; // apply() itself threw — nothing was ever applied

export type OptimisticOutcome = "confirmed" | "rolled-back" | "apply-failed";

export interface OptimisticSteps<TSnapshot, TResult> {
  /** Apply the local change instantly. Returns the snapshot to restore on
   *  failure. Throwing here aborts cleanly (nothing to roll back). */
  apply: () => TSnapshot;
  /** The real request. Its result feeds commit() so reconciliation uses
   *  server truth (e.g. the orchestrator-minted runId from approve). */
  request: () => Promise<TResult>;
  /** Success path: reconcile with the server's response/refresh. A throw
   *  here never rolls back (the server already won) — it only surfaces. */
  commit?: (result: TResult) => void | Promise<void>;
  /** Failure path: restore the pre-apply snapshot. */
  rollback: (snapshot: TSnapshot) => void;
  /** Failure/surface channel (toast, banner, inline error). */
  onError?: (message: string) => void;
  /** Optional status observer for callers that drive UI off the lifecycle. */
  onStatus?: (status: OptimisticStatus) => void;
}

/**
 * Display copy for a failed optimistic mutation: the friendly map wins for
 * known codes (M12-E2); anything else keeps the server's own message
 * verbatim — including Section 1's LAST_ADMIN protection text.
 */
export function optimisticErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return (
      friendlyErrorMessage(err.code) ??
      (err.message.trim() !== "" ? err.message : `Request failed (${err.status})`)
    );
  }
  if (err instanceof Error && err.message.trim() !== "") return err.message;
  return String(err);
}

/**
 * Framework-free engine: run one optimistic mutation to completion.
 * Returns the outcome so callers can branch (note vs error) afterwards.
 */
export async function runOptimistic<TSnapshot, TResult = unknown>(
  steps: OptimisticSteps<TSnapshot, TResult>,
): Promise<OptimisticOutcome> {
  let snapshot: TSnapshot;
  try {
    snapshot = steps.apply();
  } catch (err) {
    // Nothing was applied locally — surface honestly, roll nothing back.
    steps.onStatus?.("failed");
    steps.onError?.(optimisticErrorMessage(err));
    return "apply-failed";
  }

  steps.onStatus?.("pending");
  try {
    const result = await steps.request();
    steps.onStatus?.("confirmed");
    if (steps.commit) {
      try {
        await steps.commit(result);
      } catch (err) {
        // Reconciliation hiccup AFTER server success: never roll back the
        // user's confirmed change — surface it and stay confirmed.
        steps.onError?.(optimisticErrorMessage(err));
      }
    }
    return "confirmed";
  } catch (err) {
    steps.onStatus?.("rolled-back");
    steps.rollback(snapshot);
    steps.onError?.(optimisticErrorMessage(err));
    return "rolled-back";
  }
}

/**
 * Connected wrapper: owns the lifecycle status and guards against a stale
 * mutation finishing after a newer one started (only the newest run may
 * write status). Pass per-call steps — apply/request/rollback closures read
 * naturally like the rest of the page code.
 */
export function useOptimistic<TSnapshot = unknown, TResult = unknown>() {
  const [status, setStatus] = useState<OptimisticStatus>("idle");
  const seqRef = useRef(0);

  const mutate = useCallback(
    (steps: OptimisticSteps<TSnapshot, TResult>): Promise<OptimisticOutcome> => {
      const seq = ++seqRef.current;
      return runOptimistic<TSnapshot, TResult>({
        ...steps,
        onStatus: (next) => {
          if (seq === seqRef.current) setStatus(next);
          steps.onStatus?.(next);
        },
      });
    },
    [],
  );

  const reset = useCallback(() => setStatus("idle"), []);

  return { mutate, status, pending: status === "pending", reset };
}

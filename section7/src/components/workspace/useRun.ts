"use client";

// R2 run data: aggregate + plan + the review of the run's pull request, through
// the same API clients the old run page used. The aggregate refreshes every 3 s
// until the run settles; a failed refresh keeps the last good data and marks it
// stale. Nothing here is invented: unrecorded values stay null.

import { useCallback, useEffect, useState } from "react";
import type { AgentPlan } from "@contracts/plan";
import { ApiError, getPlan, getReviewData, type ReviewDto } from "@/lib/api";
import { getRunAggregate, runSettled, type RunAggregateDto } from "../../../app/runs/runsApi";

export type RunStatus = "loading" | "notFound" | "error" | "ready";

export interface RunView {
  status: RunStatus;
  error: string | null;
  runId: string | null;
  agg: RunAggregateDto | null;
  plan: AgentPlan | null;
  review: ReviewDto | null;
  reviewState: "absent" | "loading" | "ready" | "error";
  /** True when the last background refresh failed. */
  stale: boolean;
  retry: () => void;
  /** Quiet re-read of the aggregate and plan; what is on screen stays until the answer arrives. */
  refresh: () => Promise<void>;
}

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function useRun(runId: string | null, token: string | null): RunView {
  const [agg, setAgg] = useState<RunAggregateDto | null>(null);
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [review, setReview] = useState<ReviewDto | null>(null);
  const [reviewState, setReviewState] = useState<RunView["reviewState"]>("absent");
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [stale, setStale] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!token || !runId) return;
    let alive = true;
    setAgg(null);
    setPlan(null);
    setError(null);
    setNotFound(false);
    getRunAggregate(runId, token)
      .then((a) => alive && setAgg(a))
      .catch((err) => {
        if (!alive) return;
        if (err instanceof ApiError && (err.status === 404 || err.status === 403)) setNotFound(true);
        else setError(messageOf(err));
      });
    // The plan is the title and the plan list; a run without one is still a run.
    getPlan(runId, token)
      .then((p) => alive && setPlan(p))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [token, runId, reloadKey]);

  const refresh = useCallback(async () => {
    if (!token || !runId) return;
    try {
      setAgg(await getRunAggregate(runId, token));
      setStale(false);
    } catch {
      setStale(true);
    }
    // The plan changes when a person edits it; keep the last one if this read fails.
    await getPlan(runId, token).then(setPlan).catch(() => {});
  }, [token, runId]);

  const settled = runSettled(agg);
  useEffect(() => {
    if (!agg || settled) return;
    const id = setInterval(() => void refresh(), 3000);
    return () => clearInterval(id);
  }, [agg === null, settled, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const prId = agg?.prId ?? null;
  useEffect(() => {
    if (!token || !prId) {
      setReview(null);
      setReviewState("absent");
      return;
    }
    let alive = true;
    setReviewState("loading");
    getReviewData(prId, token)
      .then((r) => {
        if (!alive) return;
        setReview(r);
        setReviewState("ready");
      })
      .catch((err) => {
        if (!alive) return;
        setReview(null);
        setReviewState("error");
        setError((prev) => prev ?? messageOf(err));
      });
    return () => {
      alive = false;
    };
  }, [token, prId, reloadKey]);

  const retry = useCallback(() => setReloadKey((k) => k + 1), []);

  const status: RunStatus = notFound ? "notFound" : agg ? "ready" : error ? "error" : "loading";
  return { status, error, runId, agg, plan, review, reviewState, stale, retry, refresh };
}

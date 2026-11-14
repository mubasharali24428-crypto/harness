"use client";

// R1 preview data hook. Loads the seeded demo run (or a ?run= deep link) through
// the SAME API clients the live pages use — listProjects/listRuns/getRunAggregate/
// getPlan/getReviewData — and exposes one honest view state per route. No value
// on the preview routes is invented client-side: anything the backend does not
// record arrives as null and renders as "unknown"/"not recorded" downstream.
//
// Written to be reusable in R2: the hook owns data + selection only; the
// PreviewWorkspace component owns layout.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, getReviewData, listProjects, type ProjectDto, type ReviewDto } from "@/lib/api";
import { loadSession, getRunId, setProjectId, setRunId } from "@/lib/auth";
import {
  getRunAggregate,
  listRuns,
  type RunAggregateDto,
  type RunDto,
} from "../../../app/runs/runsApi";
import type { AgentPlan } from "@contracts/plan";
import { getPlan } from "@/lib/api";
import { groupRuns, pickRunId, type GroupedRuns } from "./previewData";

export type PreviewStatus =
  | "loading" // first load, nothing on screen yet
  | "signedOut" // no session in this browser
  | "empty" // signed in, team has no runs
  | "notFound" // run id requested but not visible to this team
  | "error" // a load failed; message carried separately
  | "ready"; // at least the aggregate is on screen

export interface PreviewView {
  status: PreviewStatus;
  /** Plain failure sentence for the error state; null otherwise. */
  error: string | null;
  projects: ProjectDto[];
  projectNames: Record<string, string>;
  selectedProjectId: string | null;
  runs: RunDto[];
  groups: GroupedRuns;
  runId: string | null;
  agg: RunAggregateDto | null;
  plan: AgentPlan | null;
  review: ReviewDto | null;
  /** Right-pane load state (review fetches lag the aggregate). */
  reviewState: "absent" | "loading" | "ready" | "error";
  /** Bumped by retry(); a failed load can be asked again. */
  retry: () => void;
  selectProject: (projectId: string) => void;
  /** Loads the demo seed for this team (the empty state's action). */
  seedDemoRun: () => Promise<void>;
  seeding: boolean;
  seedError: string | null;
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

export function usePreviewRun(deepLinkRunId: string | null): PreviewView {
  const [token, setToken] = useState<string | null>(null);
  const [signedOut, setSignedOut] = useState(false);

  const [projects, setProjects] = useState<ProjectDto[]>([]);
  const [runs, setRuns] = useState<RunDto[]>([]);
  const [runsLoaded, setRunsLoaded] = useState(false);

  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [runId, setRunIdState] = useState<string | null>(null);

  const [agg, setAgg] = useState<RunAggregateDto | null>(null);
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [review, setReview] = useState<ReviewDto | null>(null);
  const [reviewState, setReviewState] = useState<PreviewView["reviewState"]>("absent");

  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);

  useEffect(() => {
    const session = loadSession();
    if (!session?.token) {
      setSignedOut(true);
      return;
    }
    setToken(session.token);
  }, []);

  // Projects + runs (the sidebar's data).
  useEffect(() => {
    if (!token) return;
    let alive = true;
    void (async () => {
      setError(null);
      try {
        const [projectItems, page] = await Promise.all([
          listProjects(token),
          listRuns({ limit: 100 }, token),
        ]);
        if (!alive) return;
        setProjects(projectItems);
        setRuns(page.items);
        setRunsLoaded(true);
        // Remember the project context through the SAME accessors the live
        // pages use, so the rest of the app follows the preview's selection.
        const preferredProject =
          projectItems.find((p) => p.id === selectedProjectId) ?? projectItems[0] ?? null;
        if (preferredProject) {
          setSelectedProjectId(preferredProject.id);
          setProjectId(preferredProject.id);
        }
      } catch (err) {
        if (!alive) return;
        setRunsLoaded(true);
        setError(messageOf(err));
      }
    })();
    return () => {
      alive = false;
    };
    // selectedProjectId intentionally excluded: reselecting must not refetch the lists.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, reloadKey]);

  // Run selection once the list is in.
  useEffect(() => {
    if (!runsLoaded) return;
    const remembered = typeof window !== "undefined" ? getRunId() : null;
    const chosen = pickRunId(runs, deepLinkRunId, remembered);
    setRunIdState(chosen);
    if (chosen) {
      const run = runs.find((r) => r.id === chosen);
      if (run) {
        setSelectedProjectId(run.projectId);
        setProjectId(run.projectId);
      }
      setRunId(chosen); // shared harness.runId — same store the live pages use
    }
  }, [runsLoaded, runs, deepLinkRunId]);

  // Aggregate + plan for the chosen run.
  useEffect(() => {
    if (!token || !runId) {
      setAgg(null);
      setPlan(null);
      return;
    }
    let alive = true;
    setNotFound(false);
    setAgg(null);
    setPlan(null);
    void (async () => {
      try {
        const [aggregate, planDto] = await Promise.all([
          getRunAggregate(runId, token),
          getPlan(runId, token),
        ]);
        if (!alive) return;
        setAgg(aggregate);
        setPlan(planDto);
      } catch (err) {
        if (!alive) return;
        if (err instanceof ApiError && (err.status === 404 || err.status === 403)) {
          setNotFound(true);
        } else {
          setError(messageOf(err));
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [token, runId, reloadKey]);

  // Review for the run's PR (the diff pane's data).
  const prId = agg?.prId ?? null;
  const reviewRequested = useRef(false);
  useEffect(() => {
    if (!token || !prId) {
      setReview(null);
      setReviewState("absent");
      reviewRequested.current = false;
      return;
    }
    if (reviewRequested.current) return;
    reviewRequested.current = true;
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

  const retry = useCallback(() => {
    setError(null);
    setNotFound(false);
    setSeedError(null);
    setRunsLoaded(false);
    setReloadKey((k) => k + 1);
  }, []);

  const selectProject = useCallback((projectId: string) => {
    setSelectedProjectId(projectId);
    setProjectId(projectId);
    setRunIdState(null);
    setAgg(null);
    setPlan(null);
  }, []);

  const seedDemoRun = useCallback(async () => {
    if (!token) return;
    setSeeding(true);
    setSeedError(null);
    try {
      // Same client as the demo page (src/lib/demoApi) so the seeded ids and
      // this team's graph stay identical.
      const { seedDemo } = await import("@/lib/demoApi");
      const seeded = await seedDemo(token);
      setProjectId(seeded.projectId);
      setSelectedProjectId(seeded.projectId);
      setRunId(seeded.runId);
      setRunsLoaded(false); // force the lists to refetch with the new run
      setReloadKey((k) => k + 1);
    } catch (err) {
      setSeedError(messageOf(err));
    } finally {
      setSeeding(false);
    }
  }, [token]);

  const groups = useMemo(() => groupRuns(runs), [runs]);
  const projectNames = useMemo(
    () => Object.fromEntries(projects.map((p) => [p.id, p.name])),
    [projects],
  );

  const status: PreviewStatus = signedOut
    ? "signedOut"
    : !runsLoaded || (runId !== null && agg === null && !notFound && !error)
      ? "loading"
      : runs.length === 0 && !error
        ? "empty"
        : notFound
          ? "notFound"
          : error && !agg
            ? "error"
            : "ready";

  return {
    status,
    error,
    projects,
    projectNames,
    selectedProjectId,
    runs,
    groups,
    runId,
    agg,
    plan,
    review,
    reviewState,
    retry,
    selectProject,
    seedDemoRun,
    seeding,
    seedError,
  };
}

"use client";

// R2 sidebar data: the team's projects and runs, grouped Needs you / Working /
// Done for the selected project. Same API clients as the Runs page. Titles are
// the run's goal, which the runs list carries.

import { useCallback, useEffect, useMemo, useState } from "react";
import { listProjects, type ProjectDto } from "@/lib/api";
import { getProjectId, getToken, setProjectId } from "@/lib/auth";
import { loadErrorMessage } from "@/lib/humanize";
import { listRuns, type RunDto } from "../../../app/runs/runsApi";
import { groupRuns, type GroupedRuns } from "./runRows";

export interface RunList {
  projects: ProjectDto[];
  selectedProjectId: string | null;
  selectProject: (id: string) => void;
  groups: GroupedRuns;
  goals: Record<string, string>;
  /** False until the first list answer (or failure) arrives. */
  loaded: boolean;
  error: string | null;
  retry: () => void;
}

/** `currentRunId`: the run on screen; the sidebar follows it to its project. */
export function useRunList(currentRunId: string | null = null): RunList {
  const [projects, setProjects] = useState<ProjectDto[]>([]);
  const [runs, setRuns] = useState<RunDto[]>([]);
  const [selectedProjectId, setSelected] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    let alive = true;
    setError(null);
    Promise.all([listProjects(token), listRuns({ limit: 100 }, token)])
      .then(([projectItems, page]) => {
        if (!alive) return;
        setProjects(projectItems);
        setRuns(page.items);
        const saved = getProjectId();
        const valid = projectItems.find((p) => p.id === saved) ?? projectItems[0];
        if (valid) {
          setProjectId(valid.id);
          setSelected(valid.id);
        }
      })
      .catch((e) => alive && setError(loadErrorMessage(e)))
      .finally(() => alive && setLoaded(true));
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  useEffect(() => {
    const current = runs.find((r) => r.id === currentRunId);
    if (current) {
      setProjectId(current.projectId);
      setSelected(current.projectId);
    }
  }, [runs, currentRunId]);

  const groups = useMemo(
    () => groupRuns(selectedProjectId ? runs.filter((r) => r.projectId === selectedProjectId) : runs),
    [runs, selectedProjectId],
  );

  const goals = useMemo(
    () => Object.fromEntries(runs.flatMap((r) => (r.goal ? [[r.id, r.goal]] : []))),
    [runs],
  );

  const selectProject = useCallback((id: string) => {
    setProjectId(id);
    setSelected(id);
  }, []);
  const retry = useCallback(() => setReloadKey((k) => k + 1), []);

  return { projects, selectedProjectId, selectProject, groups, goals, loaded, error, retry };
}

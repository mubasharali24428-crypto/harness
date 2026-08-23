// W1-C2 (C-006) — post-auth session bootstrap. The login page's old
// fabricated-project / minted-run-id fallback is gone: after auth we
// resolve the caller's REAL project from Section 1 (`GET /api/v1/projects`,
// team-scoped server-side) or route to project creation. No invented state.
//
// Pure-write lane law: composes existing exports (listProjects/ApiError/
// parseApiError/IDENTITY_BASE from @/lib/api, storage setters from @/lib/auth)
// plus ONE locally-owned client for the frozen Section 1 route
// POST /api/v1/projects {name} → 201 — implemented here because src/lib/api.ts
// is outside this lane's owned paths (same pattern costApi.ts used for its
// base-URL constant).
import {
  ApiError,
  IDENTITY_BASE,
  listProjects,
  parseApiError,
  type ProjectDto,
} from "@/lib/api";
import { setProjectId } from "@/lib/auth";

export type BootstrapOutcome =
  | /** A real project was found for this team; its id is now the active one. */ "project-selected"
  /** No projects exist yet for this team — caller routes to creation. */
  | "needs-project"
  /** listProjects failed (network/service down) — nothing was invented. */
  | "unresolved";

/** True when the outcome means localStorage holds a real, existing projectId. */
export function bootstrapSucceeded(outcome: BootstrapOutcome): boolean {
  return outcome === "project-selected";
}

/**
 * Post-auth identity resolution:
 *   1. If a projectId is already stored AND still exists in the team's real
 *      project list → keep it (stale ids self-heal instead of 404ing forever).
 *   2. Else adopt the first project of the caller's team.
 *   3. Else report "needs-project" so the UI can route to creation.
 *
 * Never fabricates an id: on failure it reports honestly via "unresolved".
 * Returns the adopted ProjectDto when one was selected.
 */
export async function bootstrapProjectSession(token: string): Promise<{
  outcome: BootstrapOutcome;
  project: ProjectDto | null;
}> {
  let projects: ProjectDto[];
  try {
    projects = await listProjects(token);
  } catch {
    return { outcome: "unresolved", project: null };
  }

  const stored = getStoredProjectId();
  if (stored) {
    const kept = projects.find((p) => p.id === stored);
    if (kept) return { outcome: "project-selected", project: kept };
  }

  const first = projects[0];
  if (!first) {
    // Honest null state: no rows in S1 for this team — the login page routes
    // to the project-create flow instead of inventing an id here.
    return { outcome: "needs-project", project: null };
  }
  setProjectId(first.id);
  return { outcome: "project-selected", project: first };
}

function getStoredProjectId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem("harness.projectId");
  } catch {
    return null;
  }
}

/**
 * The creation half of C-006 ("or routes to project-create flow"). Calls the
 * frozen Section 1 route POST /api/v1/projects {name} → 201
 * {id, name, repoFullName|null} and activates the created project.
 * Body mirrors CreateProjectBody in section1/src/harness_identity/app.py;
 * repoFullName stays null (repo binding happens later, on the project page).
 */
export async function createAndActivateProject(name: string, token: string): Promise<ProjectDto> {
  const trimmed = name.trim();
  if (!trimmed) throw new Error("Project name is required.");
  const res = await fetch(`${IDENTITY_BASE}/api/v1/projects`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify({ name: trimmed }),
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    const { message, code } = parseApiError(res.status, body);
    throw new ApiError(message, code, res.status);
  }
  const project = body as ProjectDto;
  setProjectId(project.id);
  return project;
}

// M12-G8 — /projects/[projectId]/tree route (read-only repo file browser,
// part3 gap #7 slice 1). Thin Next.js server page: unwraps the [projectId]
// param and renders the client view from ./RepoTreeView (same pattern as
// runs/[runId]/page.tsx → WorkingArea). All tree UI lives there; the API
// client is ./treeApi; the S5 backend route is
// section5/src/harness_sandbox/tree_api.py.

import AppShell from "@/components/AppShell";
import { RepoTreeView } from "./RepoTreeView";

export default async function TreePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return (
    <AppShell active="/projects">
      <RepoTreeView projectId={projectId} />
    </AppShell>
  );
}

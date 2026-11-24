// Phase 7 — /projects/[projectId]/planning: admin-reviewed assignment proposals (thin server page;
// UI in ./PlanningView, client in ./planningApi, backend section4 assignment_plan.py).
import AppShell from "@/components/AppShell";
import { PlanningPanel } from "./PlanningView";

export default async function PlanningPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return (
    <AppShell active="/projects">
      <h1 style={{ fontSize: 18 }}>Assignment planning</h1>
      <PlanningPanel projectId={projectId} />
    </AppShell>
  );
}

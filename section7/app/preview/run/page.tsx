"use client";

// /preview/run — R1 screen 1: sidebar run list + the run as one scrolling
// column. Reads the seeded demo run through the same API calls the live run
// page uses (listRuns + getRunAggregate + getPlan).
import { useEffect, useState } from "react";
import PreviewWorkspace, { type PreviewPane } from "@/components/workspace/PreviewWorkspace";

export default function PreviewRunPage() {
  const [deepLinkRunId, setDeepLinkRunId] = useState<string | null>(null);
  useEffect(() => {
    const r = new URLSearchParams(window.location.search).get("run");
    setDeepLinkRunId(r);
  }, []);
  return <PreviewWorkspace pane={"none" as PreviewPane} deepLinkRunId={deepLinkRunId} />;
}

"use client";

// /preview/diff — R1 screen 2: run column + the right Diff pane (the run's PR
// diff from the review service, same call the live Changes/review surfaces use).
import { useEffect, useState } from "react";
import PreviewWorkspace from "@/components/workspace/PreviewWorkspace";

export default function PreviewDiffPage() {
  const [deepLinkRunId, setDeepLinkRunId] = useState<string | null>(null);
  useEffect(() => {
    const r = new URLSearchParams(window.location.search).get("run");
    setDeepLinkRunId(r);
  }, []);
  return <PreviewWorkspace pane="diff" deepLinkRunId={deepLinkRunId} />;
}

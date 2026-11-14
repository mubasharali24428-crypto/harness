"use client";

// /preview/review — R1 screen 3: the Diff pane with the verdict card above it
// (recorded facts first, then the diff — the review flow in one pane).
import { useEffect, useState } from "react";
import PreviewWorkspace from "@/components/workspace/PreviewWorkspace";

export default function PreviewReviewPage() {
  const [deepLinkRunId, setDeepLinkRunId] = useState<string | null>(null);
  useEffect(() => {
    const r = new URLSearchParams(window.location.search).get("run");
    setDeepLinkRunId(r);
  }, []);
  return <PreviewWorkspace pane="review" deepLinkRunId={deepLinkRunId} />;
}

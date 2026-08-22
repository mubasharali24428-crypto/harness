"use client";

import { useEffect, useRef } from "react";
import { MergeView } from "@codemirror/merge";

export default function DiffView({
  original,
  modified,
}: {
  original: string;
  modified: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<MergeView | null>(null);

  useEffect(() => {
    if (!hostRef.current || viewRef.current) return;
    const view = new MergeView({
      a: { doc: original },
      b: { doc: modified },
      parent: hostRef.current,
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [original, modified]);

  return <div ref={hostRef} className="diff-host" />;
}

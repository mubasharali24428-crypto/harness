"use client";

// Classic JSX transform under vitest/esbuild: React must be in scope for the
// .tsx extension (Next's compiler accepts the explicit import too).
import * as React from "react";
import { useEditorView, diffHighlight } from "@/lib/codemirror";
import { panelStyle, headingStyle } from "@/lib/styles";

/** What the panel says before any diff text exists. The agent's edit batches DO
 *  reach the UI as diff_progress events, so naming the files already touched is
 *  both honest and more useful than a bare "nothing here". */
export function diffEmptyState(changedPaths: string[]): string {
  if (changedPaths.length === 0) {
    return [
      "No changes yet.",
      "",
      "The agent's edits appear here as they land, and your own manual",
      "edits show up the moment you apply them.",
    ].join("\n");
  }
  const shown = changedPaths.slice(0, 12);
  const rest = changedPaths.length - shown.length;
  const lines = shown.map((p) => `  ${p}`);
  if (rest > 0) lines.push(`  … and ${rest} more`);
  return [
    `${changedPaths.length} file${changedPaths.length === 1 ? "" : "s"} changed so far:`,
    "",
    ...lines,
    "",
    "The full diff is on the Changes tab once the run opens a pull request.",
  ].join("\n");
}

export function DiffPanel({
  diffText,
  changedPaths = [],
}: {
  diffText: string;
  changedPaths?: string[];
}) {
  const doc = diffText || diffEmptyState(changedPaths);
  const containerRef = useEditorView(doc, {
    readOnly: true,
    label: "Diff of your latest edit",
    extensions: [diffHighlight],
  });

  return (
    <section style={panelStyle}>
      <h2 style={headingStyle}>Diff</h2>
      <div
        ref={containerRef}
        style={{ height: 220, border: "1px solid var(--border-soft)", borderRadius: 6, overflow: "hidden" }}
      />
    </section>
  );
}

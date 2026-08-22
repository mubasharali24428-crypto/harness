"use client";

import { useEditorView, diffHighlight } from "@/lib/codemirror";
import { panelStyle, headingStyle } from "@/lib/styles";

const EMPTY_STATE = `No live diff feed yet.

Section 5's sandbox is request/response only - it doesn't publish
in-progress diffs to the event bus, and no EventType for that exists
in the frozen contract yet. This panel shows the diff text from your
own manual edits and the most recent done-signal submission instead.`;

export function DiffPanel({ diffText }: { diffText: string }) {
  const containerRef = useEditorView(diffText || EMPTY_STATE, {
    readOnly: true,
    extensions: [diffHighlight],
  });

  return (
    <section style={panelStyle}>
      <h2 style={headingStyle}>Diff</h2>
      <div
        ref={containerRef}
        style={{ height: 220, border: "1px solid #2a3038", borderRadius: 6, overflow: "hidden" }}
      />
    </section>
  );
}

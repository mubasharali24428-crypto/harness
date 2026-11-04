"use client";

import { useEffect, useRef } from "react";
import { EditorView, basicSetup } from "codemirror";
import { EditorState } from "@codemirror/state";
import type { Extension, Range } from "@codemirror/state";
import { Decoration, ViewPlugin } from "@codemirror/view";
import type { DecorationSet, ViewUpdate } from "@codemirror/view";

/** Shared theme + diff line coloring, used by both the diff view and the manual-edit surface.
 *  Every colour comes from a theme token, and the `dark` flag follows the active
 *  `data-theme` (M-A3: it was pinned to `{ dark: true }`, so in light mode the
 *  gutter stayed charcoal against a paper editor body). */
export function editorTheme(dark: boolean): Extension {
  return EditorView.theme(
    {
      "&": { fontSize: "13px", height: "100%", backgroundColor: "var(--input)", color: "var(--text)" },
      ".cm-scroller": {
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        overflow: "auto",
      },
      ".cm-gutters": {
        backgroundColor: "var(--panel-2)",
        color: "var(--muted)",
        border: "none",
      },
      ".cm-activeLineGutter": { backgroundColor: "var(--accent-soft)", color: "var(--text)" },
      ".cm-content": { caretColor: "var(--text)" },
      ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--text)" },
      ".cm-selectionBackground, ::selection": { backgroundColor: "var(--accent-soft)" },
      ".cm-diff-add": { backgroundColor: "rgba(var(--success-rgb), 0.15)" },
      ".cm-diff-del": { backgroundColor: "rgba(var(--danger-rgb), 0.15)" },
      ".cm-diff-hunk": { color: "var(--muted)" },
      ".cm-diff-file": { color: "var(--muted)", fontWeight: "600" },
    },
    { dark },
  );
}

/** Pure: is the document currently in the dark theme? Exported for tests. */
export function editorIsDark(themeAttribute?: string | null): boolean {
  return themeAttribute !== "light";
}

/** The scroller is the region that scrolls, so it must be a keyboard stop (axe scrollable-region-focusable). */
const keyboardScroller = ViewPlugin.define((view) => {
  view.scrollDOM.tabIndex = 0;
  return {};
});

/** Pure: classifies one unified-diff line for coloring. Exported for unit tests. */
export function diffLineClass(text: string): string | null {
  if (text.startsWith("+++") || text.startsWith("---")) return "cm-diff-file";
  if (text.startsWith("+")) return "cm-diff-add";
  if (text.startsWith("-")) return "cm-diff-del";
  if (text.startsWith("@@")) return "cm-diff-hunk";
  return null;
}

function buildDiffDecorations(view: EditorView): DecorationSet {
  const ranges: Range<Decoration>[] = [];
  for (const { from, to } of view.visibleRanges) {
    let pos = from;
    while (pos <= to) {
      const line = view.state.doc.lineAt(pos);
      const cls = diffLineClass(line.text);
      if (cls) ranges.push(Decoration.line({ class: cls }).range(line.from));
      pos = line.to + 1;
    }
  }
  return Decoration.set(ranges);
}

export const diffHighlight: Extension = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = buildDiffDecorations(view);
    }
    update(update: ViewUpdate) {
      if (update.docChanged || update.viewportChanged) {
        this.decorations = buildDiffDecorations(update.view);
      }
    }
  },
  { decorations: (v) => v.decorations },
);

/** Mounts a CodeMirror 6 EditorView into a ref'd container. Re-syncs `doc` from the
 *  outside only while the editor is unfocused, so it never clobbers active typing. */
export function useEditorView(
  doc: string,
  opts: { readOnly?: boolean; onChange?: (value: string) => void; extensions?: Extension[]; label?: string } = {},
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(opts.onChange);
  onChangeRef.current = opts.onChange;
  // Latest doc, so a theme switch can rebuild the editor without losing content.
  const docRef = useRef(doc);
  docRef.current = doc;

  useEffect(() => {
    const parent = containerRef.current;
    if (!parent) return;

    const mount = () => {
      viewRef.current?.destroy();
      viewRef.current = new EditorView({
        parent,
        state: EditorState.create({
          doc: docRef.current,
          extensions: [
            basicSetup,
            editorTheme(editorIsDark(document.documentElement.getAttribute("data-theme"))),
            keyboardScroller,
            EditorView.editable.of(!opts.readOnly),
            EditorView.updateListener.of((update: ViewUpdate) => {
              if (update.docChanged) onChangeRef.current?.(update.state.doc.toString());
            }),
            // The editor body is role=textbox; without a name a screen reader announces an unlabeled field.
            ...(opts.label ? [EditorView.contentAttributes.of({ "aria-label": opts.label })] : []),
            ...(opts.extensions ?? []),
          ],
        }),
      });
    };

    mount();
    // Theme flips happen on <html data-theme>; rebuild so the editor follows.
    const observer = new MutationObserver(mount);
    observer.observe(document.documentElement, { attributeFilter: ["data-theme"] });
    return () => {
      observer.disconnect();
      viewRef.current?.destroy();
      viewRef.current = null;
    };
    // Intentionally mount-once: `doc` resync below handles external updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const view = viewRef.current;
    if (!view || view.hasFocus) return;
    const current = view.state.doc.toString();
    if (current === doc) return;
    view.dispatch({ changes: { from: 0, to: current.length, insert: doc } });
  }, [doc]);

  return containerRef;
}

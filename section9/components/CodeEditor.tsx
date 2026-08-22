import { useEffect, useRef } from "react";
import { EditorView, basicSetup } from "codemirror";
import { javascript } from "@codemirror/lang-javascript";
import { oneDark } from "@codemirror/theme-one-dark";

export default function CodeEditor({
  value,
  onChange,
  editable = true,
}: {
  value: string;
  onChange?: (v: string) => void;
  editable?: boolean;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);

  useEffect(() => {
    if (!hostRef.current || viewRef.current) return;
    const view = new EditorView({
      doc: value,
      extensions: [
        basicSetup,
        javascript(),
        oneDark,
        EditorView.editable.of(editable),
        EditorView.updateListener.of((u) => {
          if (u.docChanged && onChange) onChange(u.state.doc.toString());
        }),
      ],
      parent: hostRef.current,
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={hostRef} className="editor-host" />;
}

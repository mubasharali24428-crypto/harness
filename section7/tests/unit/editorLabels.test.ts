// Part 12 / 56: the two CodeMirror editors are role=textbox; each must carry an accessible name.
// Found with axe-core in a real browser (aria-input-field-name on .cm-content); see
// docs/briefs/evidence/s11/browser-check.md. Source-wiring test: the editor itself needs a DOM.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "src");
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

describe("CodeMirror editors have accessible names", () => {
  it("the hook turns `label` into an aria-label on the editable content", () => {
    expect(read("lib/codemirror.ts")).toMatch(/EditorView\.contentAttributes\.of\(\{\s*"aria-label":\s*opts\.label\s*\}\)/);
  });

  it("the diff view and the manual-edit editor both pass a label", () => {
    expect(read("components/DiffPanel.tsx")).toMatch(/useEditorView\([^)]*label:\s*"[^"]+"/s);
    expect(read("components/Controls.tsx")).toMatch(/useEditorView\([^)]*label:\s*"[^"]+"/s);
  });
});

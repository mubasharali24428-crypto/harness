// A2 — keyboard / screen-reader basics for the W28 scope-request editor.
// Node-env constraint (no jsdom/RTL in this repo): the REAL hook-free component
// is called as a plain function so its handlers can be invoked directly, and
// react-dom/server renders its markup. Effects that SSR cannot run (the focus
// restore on unmount) are pinned at source level, the same protocol as
// a11yAccessibleNames.test.tsx and focus.test.ts.
import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { ScopeRequestEditor } from "@/components/ScopeRequestEditor";
import * as editorModule from "@/components/ScopeRequestEditor";

const HERE = dirname(fileURLToPath(import.meta.url));
const SECTION_SRC = readFileSync(
  join(HERE, "..", "..", "app", "projects", "[projectId]", "runs", "[runId]", "scopeRequestSection.tsx"),
  "utf8",
);
const TOOL_ACTIVITY_SRC = readFileSync(
  join(HERE, "..", "..", "src", "components", "ToolActivity.tsx"), "utf8",
);

type AnyElement = React.ReactElement<Record<string, unknown>>;
type KeyEvent = { key: string; preventDefault: () => void };
type Focusable = { focus: () => void } | null | undefined;

/** Namespace access so the module still loads before the helper exists. */
const restoreTriggerFocus = (editorModule as unknown as Record<string, unknown>)
  .restoreTriggerFocus as ((el: Focusable) => void) | undefined;

function elementsOf(node: React.ReactNode): AnyElement[] {
  const out: AnyElement[] = [];
  const visit = (n: React.ReactNode): void => {
    if (Array.isArray(n)) {
      n.forEach(visit);
      return;
    }
    if (!React.isValidElement(n)) return;
    const el = n as AnyElement;
    out.push(el);
    visit((el.props as { children?: React.ReactNode }).children);
  };
  visit(node);
  return out;
}

function props(over: Record<string, unknown> = {}) {
  return {
    pathsText: "src",
    onPathsTextChange: () => {},
    onConfirm: () => {},
    onCancel: () => {},
    ...over,
  };
}

function html(): string {
  return renderToStaticMarkup(<ScopeRequestEditor {...props()} />);
}

/** The whole tag containing a marker (RTL-style attribute lookup over markup). */
function tagOf(markup: string, marker: string): string {
  const i = markup.indexOf(marker);
  if (i === -1) return "";
  const start = markup.lastIndexOf("<", i);
  return markup.slice(start, markup.indexOf(">", i) + 1);
}

/** RTL's `<label for>` branch over static markup. */
function labelForResolves(text: string, inputId: string): string | null {
  const m = text.match(
    new RegExp(`<label[^>]*(?:for|htmlFor)="${inputId}"[^>]*>([\\s\\S]*?)</label>`),
  );
  if (!m) return null;
  const label = m[1].replace(/<[^>]+>/g, "").trim();
  if (!label) return null;
  return new RegExp(`id="${inputId}"`).test(text) ? label : null;
}

function sectionKeyDown(over: Record<string, unknown> = {}): ((e: KeyEvent) => void) | undefined {
  const tree = elementsOf(ScopeRequestEditor(props(over)));
  const section = tree.find((el) => el.type === "section");
  return (section?.props as { onKeyDown?: (e: KeyEvent) => void } | undefined)?.onKeyDown;
}

describe("ScopeRequestEditor — keyboard and screen-reader basics", () => {
  it("gives every control an accessible name, and the confirm control its warning", () => {
    const markup = html();
    expect(labelForResolves(markup, "scope-request-paths")).toBe("Scope paths (one per line)");
    expect(markup).toContain(">Save scope</button>");
    expect(markup).toContain(">Cancel</button>");
    expect(markup).toContain('id="scope-request-warning"');
    expect(markup).toContain('aria-describedby="scope-request-warning"');
  });

  it("moves focus into the editor when it opens", () => {
    // "scope-paths" is unique to the textarea; the label carries the longer
    // "scope-request-paths" id and is emitted first in the markup.
    expect(tagOf(html(), "scope-paths")).toContain("autofocus");
  });

  it("hands focus back to the Edit scope button when the editor closes", () => {
    expect(typeof restoreTriggerFocus).toBe("function");
    let focusCount = 0;
    const fake = { focus: () => { focusCount += 1; } };
    restoreTriggerFocus?.(fake);
    expect(focusCount).toBe(1);
    expect(() => restoreTriggerFocus?.(null)).not.toThrow();
    // SSR never runs effects: pin the unmount cleanup in the host section.
    expect(SECTION_SRC).toContain("useRef<HTMLElement | null>");
    expect(SECTION_SRC).toContain("restoreTriggerFocus(triggerRef.current)");
    expect(SECTION_SRC).toContain("useEffect(() => () =>");
  });

  it("cancels on Escape without submitting", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    const onKeyDown = sectionKeyDown({ onCancel, onConfirm });
    expect(typeof onKeyDown).toBe("function");
    const preventDefault = vi.fn();
    onKeyDown?.({ key: "Escape", preventDefault });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(preventDefault).toHaveBeenCalledTimes(1);
  });

  it("does not submit on Enter in the text field before the confirm step", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    const onKeyDown = sectionKeyDown({ onCancel, onConfirm });
    expect(typeof onKeyDown).toBe("function");
    const preventDefault = vi.fn();
    onKeyDown?.({ key: "Enter", preventDefault });
    expect(onCancel).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
    // Enter keeps its newline behaviour in a multi-line paths field.
    expect(preventDefault).not.toHaveBeenCalled();
    expect(html()).not.toContain("<form");
    expect(html()).not.toContain('type="submit"');
  });

  it("associates the 'saving voids approvals' warning with the confirm control", () => {
    const markup = html();
    expect(tagOf(markup, "scope-save")).toContain('aria-describedby="scope-request-warning"');
    expect(markup).toContain('id="scope-request-warning"');
    expect(markup).toContain("voids the run");
    expect(markup).toContain("asks for");
    expect(markup).toContain("The request grants nothing by itself.");
  });

  it("can be opened, edited and cancelled with the keyboard alone", () => {
    // The trigger is a native button: Enter/Space open it without a mouse.
    expect(TOOL_ACTIVITY_SRC).toContain('data-testid="scope-edit-open"');
    expect(TOOL_ACTIVITY_SRC).toContain('type="button"');
    expect(TOOL_ACTIVITY_SRC).toContain("Edit scope");
    const markup = html();
    expect(tagOf(markup, "scope-paths")).toContain("autofocus");
    expect(tagOf(markup, "scope-cancel")).toContain('type="button"');

    const onPathsTextChange = vi.fn();
    const onCancel = vi.fn();
    const tree = elementsOf(ScopeRequestEditor(props({ onPathsTextChange, onCancel })));
    const textarea = tree.find((el) => el.props["data-testid"] === "scope-paths");
    (textarea?.props as { onChange: (e: unknown) => void }).onChange({
      target: { value: "src\ndocs" },
    });
    expect(onPathsTextChange).toHaveBeenCalledWith("src\ndocs");
    const cancel = tree.find((el) => el.props["data-testid"] === "scope-cancel");
    (cancel?.props as { onClick: () => void }).onClick();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(typeof (tree.find((el) => el.type === "section")?.props as
      { onKeyDown?: unknown }).onKeyDown).toBe("function");
  });
});

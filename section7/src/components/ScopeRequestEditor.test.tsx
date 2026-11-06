// W28: a shell-gate denial asks for scope; the UI must show it, name the path,
// and never write anything until a person confirms. Node-env suite: components
// are rendered with react-dom/server, and the hook-free editor is also called
// as a plain function so its handlers can be walked and invoked directly.
import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { EventType, type EventEnvelope } from "@contracts/events";
import { deriveWorkingAreaView } from "@/lib/deriveView";
import { scopeEditorPathsText, submitScopeEdit } from "@/lib/scopeRequest";
import { ScopeRequestEditor } from "./ScopeRequestEditor";
import { ToolActivity } from "./ToolActivity";

function envelope(type: EventType, payload: Record<string, unknown>, seq: number): EventEnvelope {
  return { type, payload, seq, ts: "2026-11-05T00:00:00Z", projectId: "p1", runId: "r1" };
}

function events(withRequest: boolean): EventEnvelope[] {
  const result: Record<string, unknown> = {
    callId: "c1",
    ok: false,
    error: "denied: outside run scope",
    ...(withRequest ? { scopeRequest: { path: "docs/y.txt", grants: false } } : {}),
  };
  return [
    envelope(EventType.TOOL_CALL, { callId: "c1", name: "shell", arguments: "cat docs/y.txt" }, 1),
    envelope(EventType.TOOL_RESULT, result, 2),
  ];
}

function activityHtml(withRequest: boolean): string {
  const calls = deriveWorkingAreaView(events(withRequest)).toolCalls;
  return renderToStaticMarkup(<ToolActivity calls={calls} onEditScope={() => {}} />);
}

describe("tool_result scopeRequest", () => {
  it("renders the 'needs scope: <path>' prompt and an Edit scope button", () => {
    const html = activityHtml(true);
    expect(html).toContain('data-testid="scope-request"');
    expect(html).toContain("needs scope:");
    expect(html).toContain("docs/y.txt");
    expect(html).toContain(">Edit scope</button>");
  });

  it("renders neither the prompt nor the button without a scopeRequest", () => {
    const html = activityHtml(false);
    expect(html).not.toContain("needs scope:");
    expect(html).not.toContain("Edit scope");
    expect(deriveWorkingAreaView(events(false)).toolCalls[0].scopeRequest).toBeNull();
  });

  it("ignores a malformed request (grants must be false and path non-empty)", () => {
    const malformed = events(true);
    malformed[1].payload.scopeRequest = { path: "", grants: true };
    expect(deriveWorkingAreaView(malformed).toolCalls[0].scopeRequest).toBeNull();
  });
});

type AnyElement = React.ReactElement<Record<string, unknown>>;

/** Walk the element tree returned by the hook-free editor (no DOM needed). */
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

describe("ScopeRequestEditor — confirm gate", () => {
  const pathsText = scopeEditorPathsText(["src"], "docs/y.txt");

  it("prefills current scope plus the requested path without duplicating it", () => {
    expect(pathsText).toBe("src\ndocs/y.txt");
    expect(scopeEditorPathsText(["src", "docs/y.txt"], "docs/y.txt")).toBe("src\ndocs/y.txt");
  });

  it("says plainly that saving voids approvals, asks again, and grants nothing", () => {
    const html = renderToStaticMarkup(
      <ScopeRequestEditor pathsText={pathsText} onPathsTextChange={() => {}}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(html).toContain("voids the run");
    expect(html).toContain("asks for");
    expect(html).toContain("The request grants nothing by itself.");
    // No implicit submit: there is no form and no button of type submit.
    expect(html).not.toContain("<form");
    expect(html).not.toContain('type="submit"');
    expect(html).toContain(">Save scope</button>");
  });

  it("submits nothing until Save scope is clicked, then PUTs exactly once", () => {
    const put = vi.fn(async () => ({}));
    const onPathsTextChange = vi.fn();
    const onCancel = vi.fn();
    const onConfirm = vi.fn(() => {
      void submitScopeEdit(put, { runId: "r1", token: "tok", version: 7, pathsText });
    });
    const props = { pathsText, onPathsTextChange, onConfirm, onCancel };
    const tree = elementsOf(ScopeRequestEditor(props));
    const byTestId = (id: string): AnyElement => {
      const el = tree.find((candidate) => candidate.props["data-testid"] === id);
      if (!el) throw new Error(`missing ${id}`);
      return el;
    };
    const clickOf = (id: string) => (byTestId(id).props as { onClick: () => void }).onClick;

    // Typing and cancelling are the only interactions that happen before the
    // person decides. Neither may write.
    (byTestId("scope-paths").props as { onChange: (e: unknown) => void }).onChange({
      target: { value: "src" },
    });
    clickOf("scope-cancel")();
    expect(onPathsTextChange).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();

    clickOf("scope-save")();
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(put).toHaveBeenCalledTimes(1);
    expect(put).toHaveBeenCalledWith("r1", "tok", 7, {
      scopePaths: ["src", "docs/y.txt"],
    });
  });
});

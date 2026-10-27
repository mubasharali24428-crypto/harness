/**
 * The `vscode` side of the extension (W7 slice 4).
 *
 * Everything policy-shaped lives in `core/`; this file only moves bytes and
 * asks the operator questions. If a rule ever appears here that is not "read
 * what VS Code has" or "show what the operator asked for", it belongs in the
 * core, where it is tested against a fake editor.
 */

import { execFileSync } from "node:child_process";

import * as vscode from "vscode";

import { hunkContextItems, type ContextItem } from "../core/context.js";
import type { EditorPort, PlanOffer } from "../core/run.js";

const PREVIEW_SCHEME = "harness-preview";

/** Virtual documents for the right-hand side of an inline diff. */
export class PreviewProvider implements vscode.TextDocumentContentProvider {
  private readonly contents = new Map<string, string>();
  private counter = 0;

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.contents.get(uri.path) ?? "";
  }

  add(text: string): vscode.Uri {
    this.counter += 1;
    const key = `/preview-${this.counter}.txt`;
    this.contents.set(key, text);
    return vscode.Uri.parse(`${PREVIEW_SCHEME}:${key}`);
  }
}

function workspaceRoot(): vscode.Uri {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) throw new Error("open a folder before using Harness");
  return folder.uri;
}

function fileUri(relativePath: string): vscode.Uri {
  return vscode.Uri.joinPath(workspaceRoot(), relativePath);
}

async function readText(relativePath: string): Promise<string | null> {
  try {
    const bytes = await vscode.workspace.fs.readFile(fileUri(relativePath));
    return Buffer.from(bytes).toString("utf8");
  } catch {
    return null;
  }
}

export interface EditorDeps {
  output: vscode.OutputChannel;
  status: vscode.StatusBarItem;
  preview: PreviewProvider;
}

/** The EditorPort the core drives, backed by VS Code and the operator. */
export function vscodeEditor(deps: EditorDeps): EditorPort {
  return {
    async showPlan(offer: PlanOffer): Promise<"approve" | "reject"> {
      const answer = await vscode.window.showInformationMessage(
        offer.text,
        { modal: true },
        "Approve",
        "Reject",
      );
      return answer === "Approve" ? "approve" : "reject";
    },

    report(state: string, detail?: string): void {
      deps.status.text = `$(beaker) Harness: ${state}`;
      deps.status.tooltip = detail ?? "";
      deps.output.appendLine(`[${new Date().toISOString()}] ${state}${detail ? ` — ${detail}` : ""}`);
    },

    async showDiff(
      path: string,
      before: string | null,
      after: string | null,
      stage: "proposed" | "reviewed",
    ): Promise<void> {
      const left = before === null ? deps.preview.add("") : fileUri(path);
      const right = after === null ? deps.preview.add("") : deps.preview.add(after);
      const kind = before === null ? "new file" : after === null ? "deletion" : "change";
      await vscode.commands.executeCommand(
        "vscode.diff",
        left,
        right,
        `Harness (${stage === "proposed" ? "proposed" : "reviewed"} ${kind}): ${path}`,
      );
    },

    async showPatch(path: string, patch: string): Promise<void> {
      const uri = deps.preview.add(`# ${path}\n\n${patch}\n`);
      const document = await vscode.workspace.openTextDocument(uri);
      await vscode.window.showTextDocument(document, { preview: true });
    },

    async confirmResult(summary): Promise<boolean> {
      const answer = await vscode.window.showInformationMessage(
        `Record this result (${summary.files.length} file(s), ${summary.chars} diff chars)?`,
        { modal: true },
        "Confirm",
        "Stop here",
      );
      return answer === "Confirm";
    },

    async decide(change): Promise<"approve" | "reject"> {
      const answer = await vscode.window.showInformationMessage(
        `Write the reviewed change to ${change.path}?`,
        { modal: true },
        "Apply",
        "Skip",
      );
      return answer === "Apply" ? "approve" : "reject";
    },

    readFile: readText,

    async writeFile(path: string, content: string): Promise<void> {
      const uri = fileUri(path);
      await vscode.workspace.fs.createDirectory(vscode.Uri.joinPath(uri, ".."));
      await vscode.workspace.fs.writeFile(uri, Buffer.from(content, "utf8"));
    },

    async deleteFile(path: string): Promise<void> {
      await vscode.workspace.fs.delete(fileUri(path), { useTrash: true });
    },
  };
}

/** The active selection, as a context item; null when nothing is selected. */
export function selectionItem(editor: vscode.TextEditor): ContextItem | null {
  const selection = editor.selection;
  if (selection.isEmpty) return null;
  const text = editor.document.getText(selection);
  if (!text.trim()) return null;
  const relative = vscode.workspace.asRelativePath(editor.document.uri, false);
  return {
    kind: "selection",
    path: relative,
    label: `@${relative}:${selection.start.line + 1}-${selection.end.line + 1}`,
    text,
    startLine: selection.start.line + 1,
    endLine: selection.end.line + 1,
  };
}

export function fileItem(editor: vscode.TextEditor): ContextItem {
  const relative = vscode.workspace.asRelativePath(editor.document.uri, false);
  return {
    kind: "file",
    path: relative,
    label: `@${relative}`,
    text: editor.document.getText(),
  };
}

/**
 * Uncommitted hunks of one file, as `@`-context. Parsed by the same strict
 * parser the tests pin, so a file git cannot show as text yields nothing
 * rather than a plausible-looking empty item.
 */
export function hunkItemsFor(editor: vscode.TextEditor): ContextItem[] {
  const relative = vscode.workspace.asRelativePath(editor.document.uri, false);
  let diffText = "";
  try {
    diffText = execFileSync(
      "git",
      ["-C", workspaceRoot().fsPath, "diff", "--unified=3", "--", relative],
      { encoding: "utf8", timeout: 5000 },
    );
  } catch {
    return [];  // no git, no repo or no changes: the item is simply absent
  }
  return hunkContextItems(diffText);
}

interface SymbolNode extends vscode.DocumentSymbol {}

function flattenSymbols(symbols: SymbolNode[], depth = 0): Array<{ symbol: SymbolNode; depth: number }> {
  const out: Array<{ symbol: SymbolNode; depth: number }> = [];
  for (const symbol of symbols) {
    out.push({ symbol, depth });
    if (symbol.children?.length) out.push(...flattenSymbols(symbol.children, depth + 1));
  }
  return out;
}

/** Let the operator add symbols to the context (`@`-symbol picking). */
export async function pickSymbols(editor: vscode.TextEditor): Promise<ContextItem[]> {
  const symbols = (await vscode.commands.executeCommand<SymbolNode[]>(
    "vscode.executeDocumentSymbolProvider",
    editor.document.uri,
  )) ?? [];
  if (!symbols.length) return [];
  const flat = flattenSymbols(symbols);
  const picked = await vscode.window.showQuickPick(
    flat.map(({ symbol, depth }) => ({
      label: `${"  ".repeat(depth)}${symbol.name}`,
      description: `lines ${symbol.range.start.line + 1}-${symbol.range.end.line + 1}`,
      symbol,
    })),
    { canPickMany: true, title: "Harness context: pick symbols" },
  );
  if (!picked) return [];
  const relative = vscode.workspace.asRelativePath(editor.document.uri, false);
  return picked.map(({ symbol }) => ({
    kind: "symbol" as const,
    path: relative,
    label: `@${symbol.name}`,
    text: editor.document.getText(symbol.range),
    startLine: symbol.range.start.line + 1,
    endLine: symbol.range.end.line + 1,
  }));
}

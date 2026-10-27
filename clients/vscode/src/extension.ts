/**
 * Activation for the Harness editor extension (W7 slice 4).
 *
 * The extension is a client: sign in, pick context, run, review, apply. It
 * holds a session token in VS Code's SecretStorage and nothing else — provider
 * credentials never exist on this side, because execution happens in the
 * server's confined worker, not in the extension host.
 */

import * as vscode from "vscode";
import { Harness } from "harness-ts-sdk";

import { sdkTransport } from "./adapter/sdk.js";
import {
  fileItem,
  hunkItemsFor,
  pickSymbols,
  PreviewProvider,
  selectionItem,
  vscodeEditor,
} from "./adapter/vscode.js";
import type { ContextItem } from "./core/context.js";
import { runFromEditor } from "./core/run.js";

const TOKEN_KEY = "harness.sessionToken";
const LAST_RUN_KEY = "harness.lastRun";

let output: vscode.OutputChannel;
let status: vscode.StatusBarItem;
let preview: PreviewProvider;

function config(): { orchestratorUrl: string; identityUrl: string; maxContextChars: number } {
  const settings = vscode.workspace.getConfiguration("harness");
  return {
    orchestratorUrl: settings.get<string>("serverUrl", "http://127.0.0.1:8004"),
    identityUrl: settings.get<string>("identityUrl", "http://127.0.0.1:8001"),
    maxContextChars: settings.get<number>("maxContextChars", 8000),
  };
}

async function signedIn(context: vscode.ExtensionContext): Promise<Harness | null> {
  const token = await context.secrets.get(TOKEN_KEY);
  if (!token) {
    void vscode.window.showWarningMessage("Harness: sign in first (Harness: Sign in).");
    return null;
  }
  const { orchestratorUrl, identityUrl } = config();
  return new Harness({ token, orchestratorUrl, identityUrl });
}

async function login(context: vscode.ExtensionContext): Promise<void> {
  const email = await vscode.window.showInputBox({ prompt: "Email" });
  if (!email) return;
  const { orchestratorUrl, identityUrl } = config();
  const probe = new Harness({ orchestratorUrl, identityUrl });
  await probe.requestOtp(email);
  const code = await vscode.window.showInputBox({ prompt: "Login code", password: true });
  if (!code) return;
  await probe.verifyOtp(email, code);
  if (!probe.token) throw new Error("the server did not return a session token");
  await context.secrets.store(TOKEN_KEY, probe.token);
  void vscode.window.showInformationMessage("Harness: signed in.");
}

async function collectContext(editor: vscode.TextEditor): Promise<ContextItem[]> {
  const items: ContextItem[] = [];
  const selection = selectionItem(editor);
  if (selection) {
    items.push(selection);
  } else {
    const addFile = await vscode.window.showQuickPick(
      [
        { label: "Whole file", value: "file" },
        { label: "Symbols only", value: "symbols" },
      ],
      { title: "Harness context: nothing selected — what should the run see?" },
    );
    if (addFile?.value === "file") items.push(fileItem(editor));
  }
  const withHunks = await vscode.window.showQuickPick(
    [
      { label: "Uncommitted hunks of this file", value: "hunks" },
      { label: "Nothing else", value: "none" },
    ],
    { title: "Harness context: also send the uncommitted diff?" },
  );
  if (withHunks?.value === "hunks") items.push(...hunkItemsFor(editor));
  const symbols = await pickSymbols(editor);
  items.push(...symbols);
  return items;
}

async function run(context: vscode.ExtensionContext): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    void vscode.window.showWarningMessage("Harness: open a file first.");
    return;
  }
  const harness = await signedIn(context);
  if (!harness) return;

  const items = await collectContext(editor);
  const task = await vscode.window.showInputBox({
    prompt: "What should the harness do?",
    placeHolder: "e.g. make this function handle an empty list",
  });
  if (!task) return;

  const port = vscodeEditor({ output, status, preview });
  status.show();
  try {
    const outcome = await runFromEditor(sdkTransport(harness), port, {
      task,
      context: items,
      cap: config().maxContextChars,
    });
    await context.globalState.update(LAST_RUN_KEY, outcome.runId);
    for (const refusal of outcome.refused) {
      output.appendLine(`refused ${refusal.path}: ${refusal.reason}`);
    }
    void vscode.window.showInformationMessage(
      `Harness: run ${outcome.runId} — ${outcome.applied.length} applied, ` +
        `${outcome.deleted.length} deleted, ${outcome.refused.length} refused.`,
    );
  } catch (error) {
    output.appendLine(`run failed: ${(error as Error).message}`);
    void vscode.window.showErrorMessage(`Harness: ${(error as Error).message}`);
  }
}

export function activate(context: vscode.ExtensionContext): void {
  output = vscode.window.createOutputChannel("Harness");
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.text = "$(beaker) Harness";
  status.command = "harness.status";
  status.show();
  preview = new PreviewProvider();

  context.subscriptions.push(
    output,
    status,
    vscode.workspace.registerTextDocumentContentProvider("harness-preview", preview),
    vscode.commands.registerCommand("harness.login", () => login(context)),
    vscode.commands.registerCommand("harness.logout", async () => {
      await context.secrets.delete(TOKEN_KEY);
      status.text = "$(beaker) Harness: signed out";
      void vscode.window.showInformationMessage("Harness: signed out.");
    }),
    vscode.commands.registerCommand("harness.run", () => run(context)),
    vscode.commands.registerCommand("harness.status", async () => {
      const last = context.globalState.get<string>(LAST_RUN_KEY);
      if (!last) {
        void vscode.window.showInformationMessage("Harness: no run yet in this workspace.");
        return;
      }
      const harness = await signedIn(context);
      if (!harness) return;
      const { state } = await harness.orchestrator.get_run_status(last);
      void vscode.window.showInformationMessage(`Harness: last run ${last} is ${state}.`);
    }),
  );
}

export function deactivate(): void {
  // VS Code disposes everything registered above.
}

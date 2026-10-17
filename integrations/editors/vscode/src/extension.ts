import * as vscode from "vscode";
import { HarnessClient, HarnessError, pendingHunks, redact, type Project } from "./core/protocol";

const TOKEN_KEY = "harness.sessionToken";
const PROJECT_KEY = "harness.projectId";

export function activate(context: vscode.ExtensionContext): void {
  const out = vscode.window.createOutputChannel("Harness");
  const log = async (msg: string) => out.appendLine(redact(msg, await context.secrets.get(TOKEN_KEY)));

  const client = (tokenOverride?: string) => {
    const cfg = vscode.workspace.getConfiguration("harness");
    return new HarnessClient(
      {
        identity: cfg.get<string>("identityUrl"),
        workflow: cfg.get<string>("workflowUrl"),
        orchestrator: cfg.get<string>("orchestratorUrl"),
      },
      async () => tokenOverride ?? context.secrets.get(TOKEN_KEY),
    );
  };

  const guarded = (fn: () => Promise<void>) => async () => {
    try {
      await fn();
    } catch (e) {
      const msg = e instanceof HarnessError ? e.message : "Unexpected Harness error";
      await log(`error: ${(e as Error).message}`);
      void vscode.window.showErrorMessage(`Harness: ${msg}`);
    }
  };

  const projectId = () => context.globalState.get<string>(PROJECT_KEY);
  const needProject = (): string => {
    const id = projectId();
    if (!id) throw new HarnessError("Select a project first", "NO_PROJECT", 0);
    return id;
  };

  const reg = (id: string, fn: () => Promise<void>) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, guarded(fn)));

  reg("harness.signIn", async () => {
    const token = await vscode.window.showInputBox({
      prompt: "Paste your Harness session token", password: true, ignoreFocusOut: true });
    if (!token) return;
    // Verify BEFORE storing: a wrong token must never be persisted.
    const me = await client(token.trim()).me();
    await context.secrets.store(TOKEN_KEY, token.trim());
    void vscode.window.showInformationMessage(`Harness: signed in as ${me.userId} (${me.role}) on team ${me.teamId}`);
  });

  reg("harness.signOut", async () => {
    await context.secrets.delete(TOKEN_KEY);
    await context.globalState.update(PROJECT_KEY, undefined);
    void vscode.window.showInformationMessage("Harness: signed out");
  });

  reg("harness.selectProject", async () => {
    const projects: Project[] = await client().projects();
    if (!projects.length) throw new HarnessError("No projects visible to your team", "NO_PROJECT", 0);
    const pick = await vscode.window.showQuickPick(
      projects.map((p) => ({ label: p.name ?? p.id, description: p.id, id: p.id })),
      { placeHolder: "Select a Harness project" });
    if (pick) await context.globalState.update(PROJECT_KEY, pick.id);
  });

  reg("harness.runStatus", async () => {
    const runId = await vscode.window.showInputBox({ prompt: "Run id" });
    if (!runId) return;
    const status = await client().runStatus(runId.trim());
    void vscode.window.showInformationMessage(`Run ${runId}: ${JSON.stringify(status).slice(0, 300)}`);
  });

  reg("harness.reviewChangeSets", async () => {
    const c = client();
    const sessions = await c.codeSessions(needProject());
    if (!sessions.length) throw new HarnessError("No code sessions for this project", "EMPTY", 0);
    const s = await vscode.window.showQuickPick(
      sessions.map((x) => ({ label: x.branch, description: x.sessionId, id: x.sessionId })));
    if (!s) return;
    const sets = (await c.changeSets(s.id)).filter((cs) => cs.status === "open");
    if (!sets.length) throw new HarnessError("No open change sets", "EMPTY", 0);
    const cs = await vscode.window.showQuickPick(
      sets.map((x) => ({ label: x.title, description: `${pendingHunks(x)} pending hunk(s)`, cs: x })));
    if (!cs) return;
    for (const file of cs.cs.files) {
      for (const hunk of file.hunks) {
        const current = file.decisions[hunk.id]?.decision ?? "pending";
        if (current !== "pending") continue;
        const choice = await vscode.window.showQuickPick(["Accept", "Reject", "Skip", "Stop"], {
          placeHolder: `${file.path} — ${hunk.lines.filter((l) => l.kind !== "ctx").map((l) => (l.kind === "add" ? "+" : "-") + l.text).join(" | ").slice(0, 120)}` });
        if (!choice || choice === "Stop") return;
        if (choice === "Skip") continue;
        await c.decide(cs.cs.changeSetId, [
          { path: file.path, hunkId: hunk.id, decision: choice.toLowerCase() }]);
      }
    }
    void vscode.window.showInformationMessage("Harness: review complete. Applying/merging stays in the Harness UI and your Git provider.");
  });

  reg("harness.approvePlan", async () => {
    const runId = await vscode.window.showInputBox({ prompt: "Run id" });
    const planId = runId && (await vscode.window.showInputBox({ prompt: "Plan id" }));
    if (!runId || !planId) return;
    // Approval is irreversible for the run: always an explicit, modal confirmation.
    const ok = await vscode.window.showWarningMessage(
      `Approve plan ${planId} for run ${runId}? The agent will be allowed to execute it.`,
      { modal: true }, "Approve");
    if (ok !== "Approve") return;
    await client().approvePlan(runId.trim(), planId.trim());
    void vscode.window.showInformationMessage("Harness: plan approved");
  });
}

export function deactivate(): void {}

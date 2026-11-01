#!/usr/bin/env node
/**
 * W24 exit run: the installed `.vsix`, in a real VS Code window, for one task end to end.
 *
 *     node clients/vscode/scripts/w24_window_run.mjs [--keep] [--port 9333]
 *
 * What it does, in order, against the live compose stack:
 *   1. installs `harness-vscode.vsix` into a throwaway profile (never the user's own) and opens a
 *      real VS Code window on a scratch workspace holding a one-line bug and a failing test;
 *   2. signs in through the extension's own `Harness: Sign in` (a test account on the local stack;
 *      the one-time code is read from the identity container's log, as the other drills do);
 *   3. runs `Harness: Run on selection` with a real task: plan dialog, approve, the confined local
 *      engine run (one live model run), the review summary at the confirm step, confirm, the
 *      reviewed diff, and the keyboard-first decision (`Alt+Y`);
 *   4. checks the result three ways: the file's bytes, the test passing outside the window, and
 *      the server's own records (plan approved, change approved, `diffSha` equal to the patch).
 *
 * The keys are sent into the window over the DevTools protocol, so this is an *automated* run: it
 * proves the installed extension works in a real window and that the keyboard path works, not
 * that a person finds it pleasant. The hand-run checklist is in docs/m2-w24-plan.md.
 *
 * Spend: one short model run. The provider settings come from the repo's `.env` (only the four
 * variables the local runner reads) and go to the window's process environment, never to a file,
 * the command line or the evidence.
 */

import { spawn, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { connect } from "./cdp.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../../..");
const VSIX = resolve(HERE, "../harness-vscode.vsix");
const EVIDENCE_DIR = join(REPO, "docs/briefs/evidence/w24");
const CODE_APP = "/Applications/Visual Studio Code.app";
const CODE = `${CODE_APP}/Contents/Resources/app/bin/code`;
const PROFILE = "/tmp/hvd24"; // short on purpose: macOS limits unix socket paths to ~104 characters
const WORKSPACE = "/tmp/w24ws";
const PROVIDER_VARS = ["DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DSH_MODEL", "DSH_PATCH_FILES"];
const TASK = "Fix add() in calc.py so that `python -m unittest` passes, then run `python -m unittest` to confirm.";

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const at = args.indexOf(name);
  return at === -1 ? fallback : args[at + 1];
};
const KEEP = args.includes("--keep");
const PORT = Number(flag("--port", "9333"));
const ORCHESTRATOR = flag("--orchestrator", "http://127.0.0.1:8004");
const IDENTITY = flag("--identity", "http://127.0.0.1:8001");

const runId = `w24-window-${randomBytes(6).toString("hex")}`;
const email = `${runId}@example.com`;
const sha256 = (data) => createHash("sha256").update(data).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sh = (cmd, argv, options = {}) => spawnSync(cmd, argv, { encoding: "utf8", ...options });

const evidence = {
  id: runId,
  startedAt: new Date().toISOString(),
  verdict: "FAIL",
  automated: true,
  note: "Keys were sent into the window over the DevTools protocol; no person used this window.",
  task: TASK,
  steps: [],
  captures: {},
  screenshots: [],
};
let window_ = null;
let shots = 0;

function readEnvFile() {
  const out = {};
  for (const line of readFileSync(join(REPO, ".env"), "utf8").split("\n")) {
    const found = /^([A-Z0-9_]+)=(.*)$/.exec(line.trim());
    if (found && PROVIDER_VARS.includes(found[1])) out[found[1]] = found[2].replace(/^"|"$/g, "");
  }
  return out;
}

function fetchOtp(address) {
  const id = sh("docker", ["ps", "-q", "--filter", "label=com.docker.compose.service=identity"]).stdout.split(/\s+/)[0];
  const logs = sh("docker", ["logs", "--tail", "400", id]);
  const escaped = address.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`HARNESS-OTP login code for ${escaped}: (\\d+)`, "g");
  let found;
  let last;
  while ((found = pattern.exec(`${logs.stdout}${logs.stderr}`))) last = found[1];
  return last;
}

async function http(method, url, { token, body } = {}) {
  const response = await fetch(url, {
    method,
    headers: { ...(body ? { "content-type": "application/json" } : {}), ...(token ? { authorization: `Bearer ${token}` } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const text = await response.text();
  let json;
  try { json = JSON.parse(text); } catch { json = text; }
  return { status: response.status, body: json };
}

async function serverToken() {
  await http("POST", `${IDENTITY}/api/v1/auth/request-login`, { body: { email } });
  let code;
  for (let i = 0; i < 30 && !code; i++) { code = fetchOtp(email); if (!code) await sleep(500); }
  const verified = await http("POST", `${IDENTITY}/api/v1/auth/verify-otp`, { body: { email, code } });
  if (!verified.body?.token) throw new Error(`could not sign in to verify the server's records (${verified.status})`);
  return verified.body.token;
}

async function step(name, fn) {
  const started = Date.now();
  const record = { name, ok: false, ms: 0 };
  evidence.steps.push(record);
  console.log(`> ${name}`);
  try {
    const note = await fn();
    record.ok = true;
    if (note !== undefined) record.note = note;
  } catch (error) {
    record.error = String(error?.message ?? error).slice(0, 900);
    throw error;
  } finally {
    record.ms = Date.now() - started;
  }
}

async function shot(label) {
  if (!window_) return;
  const file = `${runId}-${String(++shots).padStart(2, "0")}-${label}.png`;
  const bytes = await window_.screenshot(join(EVIDENCE_DIR, file));
  evidence.screenshots.push({ file, label, bytes });
}

async function closeWindow() {
  try {
    const info = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
    const ws = new WebSocket(info.webSocketDebuggerUrl);
    await new Promise((resolveOpen, rejectOpen) => { ws.onopen = resolveOpen; ws.onerror = rejectOpen; });
    ws.send(JSON.stringify({ id: 1, method: "Browser.close" }));
    await sleep(1500);
  } catch { /* already gone */ }
}

const said = (state) => `${state.quickInput?.title ?? ""} ${state.quickInput?.message ?? ""}`;

/** Used inside waits: an error notification means the run failed, so stop waiting for a success. */
function failOnError(state) {
  const error = state.toasts.find((t) => t.severity === "error");
  if (error) throw new Error(`the window reported an error: ${error.message}`);
}

async function main() {
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  if (!existsSync(VSIX)) throw new Error(`build the package first: ${VSIX} is missing (npm run package)`);

  // ----- preconditions: the stack, the engine image, the provider settings
  const orchestratorImage = sh("docker", ["inspect", "harness-orchestrator-1", "--format", "{{.Config.Image}}"]).stdout.trim();
  const tag = /:v(.+)$/.exec(orchestratorImage)?.[1];
  const engineImage = `harness-engine-worker:v${tag}`;
  const provider = readEnvFile();
  await step("preconditions: stack ready, engine image present, provider settings read", async () => {
    for (const url of [`${ORCHESTRATOR}/ready`, `${IDENTITY}/ready`]) {
      const ready = await http("GET", url);
      if (ready.status !== 200) throw new Error(`${url} answered ${ready.status}`);
    }
    if (!tag || sh("docker", ["image", "inspect", engineImage]).status !== 0) throw new Error(`engine image ${engineImage} is not present`);
    const missing = PROVIDER_VARS.filter((name) => !provider[name]);
    if (missing.length) throw new Error(`.env has no ${missing.join(", ")}`);
    evidence.stack = { orchestratorImage, engineImage, providerVariablesPassed: PROVIDER_VARS };
  });

  // ----- a fresh throwaway profile, the scratch workspace, the installed package
  await step("install the .vsix into a throwaway profile and open a real window", async () => {
    const taken = await fetch(`http://127.0.0.1:${PORT}/json/version`).then(() => true, () => false);
    if (taken) throw new Error(`a window is already listening on port ${PORT}; close it or pass --port`);
    sh("rm", ["-rf", PROFILE, WORKSPACE]);
    mkdirSync(join(PROFILE, "User"), { recursive: true });
    mkdirSync(WORKSPACE, { recursive: true });
    writeFileSync(join(WORKSPACE, "calc.py"), "def add(a, b):\n    return a - b\n");
    writeFileSync(join(WORKSPACE, "test_calc.py"), [
      "import unittest", "", "from calc import add", "", "",
      "class AddTest(unittest.TestCase):", "    def test_add(self):", "        self.assertEqual(add(2, 3), 5)", "", "",
      'if __name__ == "__main__":', "    unittest.main()", "",
    ].join("\n"));
    const before = sh("python3", ["-m", "unittest", "-q"], { cwd: WORKSPACE });
    if (before.status === 0) throw new Error("the scratch test passes before the run; the task would prove nothing");
    evidence.workspace = { calcBefore: sha256(readFileSync(join(WORKSPACE, "calc.py"))), testFailsBeforeRun: true };

    writeFileSync(join(PROFILE, "User/settings.json"), JSON.stringify({
      "window.dialogStyle": "custom", // dialogs rendered in the page, so they can be read back
      "window.restoreWindows": "none",
      "security.workspace.trust.enabled": false,
      "workbench.startupEditor": "none",
      "workbench.tips.enabled": false,
      "telemetry.telemetryLevel": "off",
      "update.mode": "none",
      "extensions.autoUpdate": false,
      "chat.disableAIFeatures": true,
      "harness.repoRoot": REPO,
      "harness.launcherPython": join(REPO, "section4/.venv/bin/python"),
      "harness.runTimeoutSeconds": 600,
    }, null, 2));

    const profileFlags = ["--user-data-dir", PROFILE, "--extensions-dir", join(PROFILE, "ext")];
    const installed = sh(CODE, [...profileFlags, "--install-extension", VSIX]);
    const listed = sh(CODE, [...profileFlags, "--list-extensions", "--show-versions"]).stdout.trim().split("\n").filter((l) => l.includes("@"));
    const version = sh(CODE, ["--version"]).stdout.trim().split("\n");
    const extDir = readdirSync(join(PROFILE, "ext")).find((d) => d.startsWith("harness.harness-vscode"));
    evidence.environment = {
      vscode: version[0], vscodeCommit: version[1], arch: version[2], node: process.version, platform: process.platform,
      extension: {
        installOutput: installed.stdout.trim().split("\n").pop(),
        listed, installedUnder: extDir ? join("<throwaway profile>/ext", extDir) : null,
        vsixSha256: sha256(readFileSync(VSIX)), vsixBytes: readFileSync(VSIX).length,
      },
    };
    if (!listed.some((l) => l.startsWith("harness.harness-vscode@"))) throw new Error(`the extension is not installed: ${installed.stdout}${installed.stderr}`);

    // The window inherits the provider settings through its environment only.
    const child = spawn(CODE, [...profileFlags, `--remote-debugging-port=${PORT}`, "--new-window", WORKSPACE, join(WORKSPACE, "calc.py")], {
      env: { ...process.env, ...provider, HARNESS_IMAGE_TAG: tag }, stdio: "ignore", detached: true,
    });
    child.unref();
    for (let i = 0; i < 60 && !window_; i++) {
      try { window_ = await connect(PORT); } catch { await sleep(1000); }
    }
    if (!window_) throw new Error("the VS Code window did not come up");
    await window_.waitFor("the Harness status item (the extension activated)", (s) => s.status.some((t) => /Harness/.test(t)), { timeoutMs: 60000 });
    await shot("window-open");
    return evidence.environment.extension.listed;
  });

  // ----- sign in through the extension's own command
  await step("Harness: Sign in (email, one-time code, signed-in notice)", async () => {
    await window_.runCommand("Harness: Sign in");
    await window_.waitFor("the email box", (s) => /^Email/.test(s.quickInput?.message ?? ""), { timeoutMs: 15000 });
    await window_.type(email);
    await window_.press("Enter");
    await window_.waitFor("the login code box", (s) => /^Login code/.test(s.quickInput?.message ?? ""), { timeoutMs: 20000 });
    let code;
    for (let i = 0; i < 30 && !code; i++) { code = fetchOtp(email); if (!code) await sleep(500); }
    if (!code) throw new Error("no one-time code in the identity log");
    await window_.type(code);
    await window_.press("Enter");
    const { found } = await window_.waitFor("the signed-in notice", (s) => s.toasts.find((t) => /signed in/i.test(t.message)), { timeoutMs: 20000 });
    return found.message;
  });

  // ----- the task: context, task text, plan dialog
  let planKey = "keyboard Enter on the default button";
  await step("Harness: Run on selection: context, task, plan dialog", async () => {
    await window_.runCommand("Harness: Run on selection");
    await window_.waitFor("the context choice", (s) => /nothing selected/i.test(said(s)), { timeoutMs: 15000 });
    await window_.press("Enter"); // Whole file
    await window_.waitFor("the uncommitted-diff choice", (s) => /uncommitted diff/i.test(said(s)), { timeoutMs: 15000 });
    await window_.press("ArrowDown");
    await window_.press("Enter"); // Nothing else
    await window_.waitFor("the task box", (s) => /What should the harness do/i.test(said(s)), { timeoutMs: 20000 });
    await window_.type(TASK);
    await window_.press("Enter");
    const { state } = await window_.waitFor("the plan dialog", (s) => s.dialog && s.dialog.buttons.includes("Approve"), { timeoutMs: 40000 });
    evidence.captures.planDialog = state.dialog;
    if (!`${state.dialog.message} ${state.dialog.detail}`.includes("Fix add() in calc.py")) throw new Error("the plan dialog does not show the goal");
    await shot("plan-dialog");
    await window_.press("Enter"); // the default button is Approve
    await sleep(800);
    if ((await window_.ui()).dialog?.buttons.includes("Approve")) {
      planKey = "DOM click on Approve (Enter did not take)";
      await window_.clickDialogButton("Approve");
    }
    return planKey;
  });

  // ----- the confined local run; wait for the confirm step with the review summary
  await step("approved plan runs in the container boundary; the confirm step shows the review summary", async () => {
    const seen = [];
    const { state } = await window_.waitFor("the confirm dialog", (s) => {
      failOnError(s);
      const text = s.status.find((t) => /^Harness/.test(t));
      if (text && seen.at(-1)?.text !== text) seen.push({ at: new Date().toISOString(), text });
      return s.dialog && s.dialog.buttons.includes("Confirm");
    }, { timeoutMs: 11 * 60 * 1000, everyMs: 1000 });
    evidence.captures.statusDuringRun = seen;
    evidence.captures.confirmDialog = state.dialog;
    await shot("confirm-with-review-summary");
    const detail = state.dialog.detail;
    for (const heading of ["Changed files", "Tests", "Failures:", "Cost:", "Time:"]) {
      if (!detail.includes(heading)) throw new Error(`the review summary has no "${heading}" line`);
    }
    return `${seen.length} status changes; summary shown`;
  });

  await step("Confirm (the done-signal); the reviewed diff opens with its decision prompt", async () => {
    await window_.press("Enter"); // the default button is Confirm
    await sleep(800);
    if ((await window_.ui()).dialog?.buttons.includes("Confirm")) await window_.clickDialogButton("Confirm");
    const { state } = await window_.waitFor("the review prompt", (s) => {
      failOnError(s);
      return s.toasts.find((t) => /write the reviewed change/i.test(t.message));
    }, { timeoutMs: 120000 });
    evidence.captures.reviewPrompt = { toasts: state.toasts, status: state.status.filter((t) => /Harness/.test(t)), tabs: state.tabs };
    if (!state.status.some((t) => /Harness review 1\/1: calc\.py/.test(t))) throw new Error("the status bar does not show the review position");
    await shot("review-prompt");
  });

  await step("Alt+Y applies the reviewed change from the keyboard", async () => {
    // Keyboard first. If the keystroke does not register through the protocol, fall back to the
    // command palette and then the notification's button, and record which one actually answered.
    const answered = (s) => s.toasts.some((t) => /Harness: run .* applied/.test(t.message));
    const attempts = [
      ["Alt+Y", () => window_.press("y", { alt: true })],
      ["command palette: Harness: Apply reviewed change", () => window_.runCommand("Harness: Apply reviewed change")],
      ["notification button: Apply", () => window_.clickToastButton("Apply")],
    ];
    evidence.captures.decisionAttempts = [];
    for (const [via, act] of attempts) {
      await act();
      const settled = await window_.waitFor("the review answer", (s) => { failOnError(s); return answered(s); }, { timeoutMs: 30000 })
        .then(() => true, (error) => {
          if (/reported an error/.test(error.message)) throw error; // a failure is not "the key did not register"
          return false;
        });
      evidence.captures.decisionAttempts.push({ via, answered: settled });
      if (settled) { evidence.decisionVia = via; break; }
    }
    if (!evidence.decisionVia) throw new Error("no way of answering the review prompt took effect");
    const { found } = await window_.waitFor("the final notice", (s) => s.toasts.find((t) => /Harness: run .* applied/.test(t.message)), { timeoutMs: 60000 });
    evidence.captures.finalNotice = found.message;
    evidence.runId = /run (\S+) /.exec(found.message)?.[1] ?? null;
    if (!/1 applied, 0 deleted, 0 refused/.test(found.message)) throw new Error(`unexpected outcome: ${found.message}`);
    await shot("applied");
    return found.message;
  });

  // ----- checks outside the window
  await step("the file, the test and the server's records agree", async () => {
    const calc = readFileSync(join(WORKSPACE, "calc.py"), "utf8");
    const after = sh("python3", ["-m", "unittest", "-q"], { cwd: WORKSPACE });
    evidence.workspace = { ...evidence.workspace, calcAfter: sha256(calc), calcAfterContent: calc, testPassesAfterRun: after.status === 0, unittestTail: `${after.stdout}${after.stderr}`.trim().slice(-200) };
    if (!/return a \+ b/.test(calc) || after.status !== 0) throw new Error(`the fix is not in the file or the test still fails: ${JSON.stringify(calc)}`);

    const token = await serverToken();
    const base = `${ORCHESTRATOR}/api/v1/orchestrator/runs/${evidence.runId}`;
    const [run, changes, diff] = await Promise.all([http("GET", base, { token }), http("GET", `${base}/changes`, { token }), http("GET", `${base}/diff`, { token })]);
    const items = changes.body?.items ?? [];
    const files = diff.body?.files ?? [];
    evidence.server = {
      runState: run.body?.state, planApproved: run.body?.planApproved,
      changes: items.map((c) => ({ path: c.path, changeKind: c.changeKind, reviewState: c.reviewState, diffSha: c.diffSha })),
      diff: files.map((f) => ({ path: f.path, diffSha: f.diffSha, patchSha256: sha256(f.patch ?? ""), digestMatchesPatch: sha256(f.patch ?? "") === f.diffSha })),
    };
    if (run.status !== 200 || items.length !== 1 || items[0].reviewState !== "approved") throw new Error(`server records disagree: ${JSON.stringify(evidence.server)}`);
    if (!evidence.server.diff.every((f) => f.digestMatchesPatch) || files[0]?.diffSha !== items[0].diffSha) throw new Error("the approved digest is not the digest of the recorded patch");
    // A local run is not metered by the orchestrator (the model call is made from the machine, not
    // through the router), so zero rows is the expected answer. Read-only, and never fails the run.
    const ledger = sh("docker", ["exec", "harness-db-1", "sh", "-c", `psql -U "\${POSTGRES_USER:-postgres}" -d "\${POSTGRES_DB:-harness}" -Atc "select count(*) from cost_ledger where run_id='${evidence.runId}'"`]);
    const rows = Number(ledger.stdout.trim());
    evidence.server.costLedgerRowsForThisRun = Number.isInteger(rows) ? rows : `unavailable (${ledger.stderr.trim().slice(0, 120)})`;
    return evidence.server.changes;
  });

  evidence.verdict = "PASS";
}

let failure = null;
try {
  await main();
} catch (error) {
  failure = error;
  evidence.error = String(error?.message ?? error).slice(0, 1200);
  try { await shot("FAIL"); } catch { /* the window may be gone */ }
} finally {
  evidence.finishedAt = new Date().toISOString();
  if (window_) { try { window_.close(); } catch { /* ignore */ } }
  if (!KEEP) {
    await closeWindow();
    sh("rm", ["-rf", PROFILE, WORKSPACE]);
  }
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  const out = join(EVIDENCE_DIR, `${runId}.json`);
  writeFileSync(out, `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(JSON.stringify({ verdict: evidence.verdict, evidence: out, runId: evidence.runId ?? null, error: evidence.error }, null, 2));
}
process.exit(failure ? 1 : 0);

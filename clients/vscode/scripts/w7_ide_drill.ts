#!/usr/bin/env -S npx tsx
/**
 * W7 live gate: an edit proposed in the editor round-trips through the policy gate.
 *
 * This drives the extension's *core* (the same code `extension.ts` calls)
 * against the live stack, with a directory-backed fake editor instead of a
 * VS Code window:
 *
 *   1. a selection becomes context, and the goal on the wire carries it;
 *   2. the plan is approved through the server gate and the run starts — and a
 *      confined worker container is observed while it executes;
 *   3. the diff is shown from the server's patch, whose digest the review names;
 *   4. a review against different bytes is refused by the server (CHANGE_STALE);
 *   5. the approved bytes land on disk, and the change is recorded approved with
 *      that digest.
 *
 * The `vscode`-bound adapter is typechecked but not exercised here; this script
 * says so in its evidence rather than implying a window was opened.
 *
 *     cd clients/vscode && npx tsx scripts/w7_ide_drill.ts [--timeout-min 20]
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { Harness } from "harness-ts-sdk";

import { localEngineExecution } from "../src/adapter/local.js";
import { sdkTransport } from "../src/adapter/sdk.js";
import { sha256Hex } from "../src/core/diff.js";
import type { ChangeView, EditorPort, PlanOffer } from "../src/core/run.js";
import { runFromEditor } from "../src/core/run.js";

const IDENTITY = "http://127.0.0.1:8001";
const ORCHESTRATOR = "http://127.0.0.1:8004";
const WORKER_RE = /^harness-[0-9a-f]{32}(-relay)?$/;

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const OUT = join(REPO, "docs", "briefs", "evidence", "pilot-prep");

/**
 * Combined stdout+stderr. `docker logs` writes a container's stderr to its own
 * stderr, and the identity service prints the login code there — reading only
 * stdout returns nothing and looks like "no code was sent" (the first run of
 * this drill failed exactly that way, with the code plainly in the logs).
 */
function sh(argv: string[]): string {
  const result = spawnSync(argv[0]!, argv.slice(1), { encoding: "utf8", timeout: 30_000 });
  if (result.error) throw new Error(`${argv.join(" ")}: ${result.error.message}`);
  return `${result.stdout ?? ""}${result.stderr ?? ""}`;
}

async function post<T>(url: string, body: unknown, token?: string): Promise<{ status: number; json: T }> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, json: (text ? JSON.parse(text) : {}) as T };
}

async function otpFromLogs(email: string): Promise<string> {
  const ids = sh(["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=identity"])
    .split(/\s+/)
    .filter(Boolean);
  if (!ids.length) throw new Error("identity container not found");
  const pattern = new RegExp(`HARNESS-OTP login code for ${email.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}: (\\d+)`);
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const logs = sh(["docker", "logs", "--tail", "400", ids[0]!]);
    const match = pattern.exec(logs);
    if (match) return match[1]!;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`no OTP for ${email} in the identity logs`);
}

interface EditorTrace {
  plans: PlanOffer[];
  diffs: Array<{ path: string; stage: string; before: string | null; after: string | null }>;
  patches: string[];
  confirmations: Array<{ files: string[]; chars: number }>;
  states: string[];
  writes: string[];
  deletes: string[];
}

function dirEditor(root: string, trace: EditorTrace): EditorPort {
  const absolute = (rel: string) => join(root, rel);
  return {
    async showPlan(offer: PlanOffer): Promise<"approve" | "reject"> {
      trace.plans.push(offer);
      return "approve";
    },
    report(state: string): void {
      trace.states.push(state);
    },
    async showDiff(path, before, after, stage): Promise<void> {
      trace.diffs.push({ path, stage, before, after });
    },
    async showPatch(path, patch): Promise<void> {
      trace.patches.push(path);
      trace.diffs.push({ path, stage: "patch", before: null, after: patch });
    },
    async confirmResult(summary): Promise<boolean> {
      trace.confirmations.push(summary);
      return true;
    },
    async decide(): Promise<"approve" | "reject"> {
      return "approve";
    },
    async readFile(rel: string): Promise<string | null> {
      return existsSync(absolute(rel)) ? readFileSync(absolute(rel), "utf8") : null;
    },
    async writeFile(rel: string, content: string): Promise<void> {
      mkdirSync(dirname(absolute(rel)), { recursive: true });
      writeFileSync(absolute(rel), content, "utf8");
      trace.writes.push(rel);
    },
    async deleteFile(rel: string): Promise<void> {
      throw new Error(`the drill expects no deletions, got ${rel}`);
    },
  };
}

function startWorkerPoller(): { seen: Record<string, unknown>; stop: () => void } {
  const seen: Record<string, unknown> = {};
  const timer = setInterval(() => {
    const lines = sh(["docker", "ps", "--format", "{{.ID}} {{.Names}}"]).split("\n");
    for (const line of lines) {
      const [id, name] = line.trim().split(" ");
      if (!id || !name || !WORKER_RE.test(name) || seen[name]) continue;
      const raw = sh(["docker", "inspect", id]);
      const jsonStart = raw.indexOf("[");
      if (jsonStart < 0) continue;
      let info: Record<string, any>;
      try {
        info = JSON.parse(raw.slice(jsonStart))[0];
      } catch {
        continue;  // never let a malformed inspect hide the observation
      }
      seen[name] = {
        role: name.endsWith("-relay") ? "relay" : "worker",
        image: info.Config?.Image,
        networks: Object.keys(info.NetworkSettings?.Networks ?? {}),
        // `Internal` is a property of the *network*, and the container's
        // endpoint block does not carry it — reading it there reports false for
        // a genuinely internal network. Ask the network itself.
        internalNetworks: Object.keys(info.NetworkSettings?.Networks ?? {}).filter(
          (name: string) => {
            const netJson = sh(["docker", "network", "inspect", name]);
            const at = netJson.indexOf("[");
            if (at < 0) return false;
            try {
              return Boolean(JSON.parse(netJson.slice(at))[0]?.Internal);
            } catch {
              return false;
            }
          },
        ),
        readOnlyRoot: Boolean(info.HostConfig?.ReadonlyRootfs),
        capDrop: info.HostConfig?.CapDrop,
        entrypoint: info.Config?.Entrypoint,
      };
    }
  }, 1000);
  return { seen, stop: () => clearInterval(timer) };
}

async function main(): Promise<number> {
  const timeoutArg = process.argv.indexOf("--timeout-min");
  const timeoutMin = timeoutArg > 0 ? Number(process.argv[timeoutArg + 1]) : 20;

  const stamp = Math.floor(Date.now() / 1000);
  const email = `w7-ide-${stamp}@example.com`;

  const request = await post(`${IDENTITY}/api/v1/auth/request-login`, { email });
  if (request.status !== 200) throw new Error(`request-login ${request.status}`);
  const code = await otpFromLogs(email);
  const verified = await post<{ token?: string }>(`${IDENTITY}/api/v1/auth/verify-otp`, {
    email,
    code,
  });
  const token = verified.json.token;
  if (verified.status !== 200 || !token) throw new Error(`verify-otp ${verified.status}`);

  const harness = new Harness({ token, identityUrl: IDENTITY, orchestratorUrl: ORCHESTRATOR });
  const project = await harness.http.request<{ id?: string }>(
    IDENTITY,
    "/api/v1/projects",
    "POST",
    { name: `w7 ide ${stamp}` },
  );
  const projectId = project?.id ?? null;
  if (!projectId) throw new Error("create project returned no id");

  // A real workspace on disk: the run executes server-side, and only the
  // reviewed bytes are written here.
  const workspace = mkdtempSync(join(tmpdir(), "w7-ide-"));
  mkdirSync(join(workspace, "src"), { recursive: true });
  const selectionText = "def greet(name):\n    return f\"hello {name}\"\n";
  writeFileSync(join(workspace, "src", "note.py"), selectionText, "utf8");

  const trace: EditorTrace = {
    plans: [],
    diffs: [],
    patches: [],
    confirmations: [],
    states: [],
    writes: [],
    deletes: [],
  };
  const editor = dirEditor(workspace, trace);

  const transport = sdkTransport(harness, projectId);
  const execution = localEngineExecution({
    repoRoot: REPO,
    directory: workspace,
    python: process.env.HARNESS_LAUNCHER_PYTHON ?? "python3",
    timeoutSeconds: timeoutMin * 60,
  });

  const poller = startWorkerPoller();
  const outcome = await runFromEditor(transport, editor, {
    execution,
    task:
      "Create a file named ide_note.txt in the workspace root whose only content is the " +
      "line: reviewed in the editor. Do not change any other file.",
    context: [
      {
        kind: "selection",
        path: "src/note.py",
        label: "@src/note.py:1-2",
        text: selectionText.replace(/\n+$/, ""),
        startLine: 1,
        endLine: 2,
      },
    ],
  }).finally(() => poller.stop());

  const appliedPath = outcome.applied[0] ?? null;
  const appliedText = appliedPath ? readFileSync(join(workspace, appliedPath), "utf8") : null;
  const proposed = trace.diffs.find((d) => d.stage === "proposed") ?? null;
  const shown = trace.diffs.find((d) => d.stage === "reviewed") ?? proposed;

  // The server's record of the same run: the goal carried the selection, and
  // the change is approved with the digest the editor reviewed.
  const changes = await transport.changes(outcome.runId);
  const change = changes.find((c: ChangeView) => c.path === appliedPath) ?? changes[0] ?? null;

  let wrongDigest: { refused: boolean; code?: string; status?: number } = { refused: false };
  if (change) {
    try {
      await harness.orchestrator.review_change(outcome.runId, change.id, {
        decision: "rejected",
        diffSha: sha256Hex("not the reviewed bytes"),
      });
      wrongDigest = { refused: false };
    } catch (error) {
      const apiError = error as { status?: number; code?: string; message?: string };
      wrongDigest = { refused: true, status: apiError.status, code: apiError.code };
    }
  }

  // The gate stores the approved plan in agent_plans (plan_versions is the
  // per-run history the queue path writes); the goal the model was given lives
  // here verbatim, which is what makes "what did it see" answerable.
  const planRow = sh([
    "docker",
    "exec",
    sh(["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=db"]).trim(),
    "sh",
    "-lc",
    `psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select goal from agent_plans where run_id='${outcome.runId}'"`,
  ]);
  const goal = planRow.trim();

  const eventTypes = sh([
    "docker",
    "exec",
    sh(["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=db"]).trim(),
    "sh",
    "-lc",
    `psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select type from agent_events where run_id='${outcome.runId}' order by seq"`,
  ])
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const workers = Object.values(poller.seen) as Array<Record<string, unknown>>;
  const worker = workers.find((w) => w.role === "worker");

  const evidence = {
    date: new Date().toISOString().slice(0, 10),
    week: "W7",
    claim: "an edit proposed in the editor round-trips through the policy gate; the extension is not a bypass",
    drive: "extension core (clients/vscode/src/core) against the live stack; vscode adapter typechecked, not opened",
    runId: outcome.runId,
    state: outcome.state,
    confinement: outcome.confinement,
    image: outcome.image,
    resultConfirmedByOperator: outcome.confirmed,
    goalCarriedSelection: goal.includes("@src/note.py:1-2") && goal.includes("hello {name}"),
    goalChars: goal.length,
    planApprovedInEditor: trace.plans.length === 1,
    proposedDiffSeen: proposed !== null,
    planTextPreview: trace.plans[0]?.text.slice(0, 300),
    statesSeen: [...new Set(trace.states)],
    serverEventTypes: eventTypes,
    // Recorded, not smoothed over: an IDE run executes off-host, so the server
    // holds the plan gate and the completion, and no execution_started row.
    auditShape: "plan_proposed + plan_approved + done_signal_received + review_* (no execution_started; off-host execution cannot mint tool events)",
    confinedWorker: worker ?? null,
    workerObserved: Boolean(worker),
    diffStages: trace.diffs.map((d) => `${d.stage}:${d.path}`),
    patchesShownInstead: trace.patches,
    doneSignalConfirmed: trace.confirmations.length === 1,
    diffShown: shown ? { path: shown.path, beforeNull: shown.before === null } : null,
    reviewedDigest: change?.diffSha ?? null,
    reviewState: change?.reviewState ?? null,
    wrongDigestReview: wrongDigest,
    applied: outcome.applied,
    appliedText,
    refused: outcome.refused,
    workspace,
  };

  const checks = {
    ran: outcome.state === "done",
    goalCarriedSelection: evidence.goalCarriedSelection,
    planApproved: evidence.planApprovedInEditor,
    workerObserved: evidence.workerObserved && Boolean(worker?.readOnlyRoot),
    workerOnInternalNetwork:
      Boolean(worker) &&
      Array.isArray((worker as Record<string, unknown>).internalNetworks) &&
      ((worker as Record<string, unknown>).internalNetworks as unknown[]).length > 0,
    confinementNamed: outcome.confinement === "container",
    diffShown: evidence.diffShown !== null,
    wrongDigestRefused: wrongDigest.refused && wrongDigest.code === "CHANGE_STALE",
    reviewApprovedWithDigest: change?.reviewState === "approved",
    appliedBytesMatch: (appliedText ?? "").trimEnd() === "reviewed in the editor",
    noRefusals: outcome.refused.length === 0,
  };
  const verdict = Object.values(checks).every(Boolean) ? "PASS" : "FAIL";

  mkdirSync(OUT, { recursive: true });
  const path = join(OUT, `w7-ide-${stamp}.json`);
  writeFileSync(path, JSON.stringify({ ...evidence, checks, verdict }, null, 2) + "\n", "utf8");

  console.log(JSON.stringify({ checks, verdict, appliedText, worker: worker ?? null }, null, 1));
  console.log(`${verdict}  evidence -> ${relative(REPO, path)}`);
  return verdict === "PASS" ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(error);
    process.exit(1);
  },
);

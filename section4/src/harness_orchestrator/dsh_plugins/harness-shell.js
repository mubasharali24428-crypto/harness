// Runtime plugin that replaces the DeepSeek runtime's persistent bash tool (part 52, B1).
//
// Every `bash` call goes to Section 5's shell gate (section5/src/harness_sandbox/shell_gate.py):
// allow-list, no shell operators, forbidden paths, the run intent's scope paths, OS confinement.
// A guard counts every tool call of the run and denies calls past `maxToolCalls` (part 44).
// The engine inserts this file per run (engine._execution_policy_patch); it needs no npm install.
import { spawn } from "node:child_process";

export const name = "harness-shell";
export const inject = ["tools", "workingDirectory"];

const DESCRIPTION = `Run one command in the workspace. Every call is checked by the harness command policy.
* One program per call: no pipes, ;, &&, redirection, backgrounding or $(...). Each call starts fresh; no shell state carries over.
* Allowed programs: ls, cat, head, tail, find, grep, rg, pwd, echo, git (status, diff, log, show, branch, checkout, switch, add, commit, reset, clean, stash, fetch, pull, rev-parse), python, python3, pytest, npm, npx, yarn, pnpm, vitest, jest, cargo, go, ruff, flake8, mypy, black, isort, eslint, prettier, tsc.
* A refused command returns the reason; change the command rather than repeating it.`;

function runGate(gate, request, signal) {
  return new Promise((resolve, reject) => {
    // The runtime's environment carries the model API key: the gate gets only what it needs.
    const child = spawn(gate[0], gate.slice(1), {
      env: { PATH: process.env.PATH ?? "/usr/bin:/bin", HOME: process.env.HOME ?? "/tmp" },
      stdio: ["pipe", "pipe", "pipe"],
    });
    const onAbort = () => child.kill("SIGTERM");
    signal.addEventListener("abort", onAbort, { once: true });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => { out += d; });
    child.stderr.on("data", (d) => { err += d; });
    child.on("error", reject);
    child.on("close", (code) => {
      signal.removeEventListener("abort", onAbort);
      try {
        resolve(JSON.parse(out.trim().split("\n").pop()));
      } catch {
        reject(new Error(`shell gate failed (exit ${code}): ${err.slice(-500)}`));
      }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

export function apply(ctx, config) {
  const seen = new Set();
  ctx.tools.guard((exec) => {
    if (config.maxToolCalls == null) return undefined;
    seen.add(exec.callId);
    return seen.size > config.maxToolCalls
      ? `run tool-call limit reached (${config.maxToolCalls} calls); finish with what you have`
      : undefined;
  });
  ctx.tools.register({
    name: "bash",
    description: DESCRIPTION,
    parameters: {
      type: "object",
      properties: { command: { type: "string", description: "The command to run" } },
      required: ["command"],
    },
    output: { schema: { type: "string" }, render: (_args, value) => [{ type: "text", text: value }] },
    async execute(args, exec) {
      if (typeof args?.command !== "string") throw new Error("command must be a string");
      const cwd = await ctx.workingDirectory.ensure(exec.agent, exec.signal);
      const res = await runGate(config.gate, {
        command: args.command,
        cwd,
        forbiddenPaths: config.forbiddenPaths ?? [],
        scopePaths: config.scopePaths ?? [],
        timeoutSeconds: config.timeoutSeconds ?? 300,
      }, exec.signal);
      if (res.denied) throw new Error(`Denied by the harness command policy: ${res.denied}`);
      return `${res.output}\n[exit code: ${res.exitCode ?? "timeout"}]`;
    },
  });
}

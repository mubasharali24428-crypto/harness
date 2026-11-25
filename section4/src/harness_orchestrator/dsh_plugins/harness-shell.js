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
* To search file contents use the grep tool, not bash. Never add 2>/dev/null, &&, ;, | or > to a command: they are always refused and waste a turn. The working directory is already the workspace root, so pwd is not needed.
* One program per call: no pipes, ;, &&, redirection, backgrounding or $(...). Each call starts fresh; no shell state carries over.
* Allowed programs: ls, cat, head, tail, find, grep, rg, pwd, echo, git (status, diff, log, show, branch, checkout, switch, add, commit, reset, clean, stash, fetch, pull, rev-parse), python, python3, pytest, npm, npx, yarn, pnpm, vitest, jest, cargo, go, ruff, flake8, mypy, black, isort, eslint, prettier, tsc.
* A refused command returns the reason; change the command rather than repeating it.`;

// W27: denials in a row mean the model is probing, not working. Codex's documented thresholds:
// 3 consecutive, or 10 within the last 50 calls. Once tripped it stays tripped for the run.
export function denialBreaker(consecutive = 3, windowHits = 10, windowSize = 50) {
  const recent = [];
  let run = 0;
  let tripped = false;
  return {
    record(denied) {
      recent.push(denied);
      if (recent.length > windowSize) recent.shift();
      run = denied ? run + 1 : 0;
      if (run >= consecutive || recent.filter(Boolean).length >= windowHits) tripped = true;
      return tripped;
    },
    get tripped() { return tripped; },
  };
}

const BREAKER_MESSAGE = "this run has been stopped after repeated denials by the harness command policy; "
  + "do not try other commands. Tell the user what you were trying to do and which scope or permission is missing.";

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
  const breaker = denialBreaker();
  ctx.tools.guard((exec) => {
    if (breaker.tripped) return BREAKER_MESSAGE;
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
      // A timeout is not a denial: it does not count toward the breaker and says nothing about safety.
      breaker.record(Boolean(res.denied));
      if (res.denied) {
        // W27: reason, then what to do instead. Retrying or switching model is never suggested.
        const next = res.alternative ? ` Instead: ${res.alternative}` : "";
        const ask = res.scopeRequest ? ` [scope-request] ${JSON.stringify(res.scopeRequest)}` : "";
        const stop = breaker.tripped ? ` ${BREAKER_MESSAGE}` : "";
        throw new Error(`Denied by the harness command policy: ${res.denied}.${next}${ask}${stop}`);
      }
      if (res.timedOut) {
        return `${res.output}\n[timed out: the command ran too long and was stopped. This is not a policy denial; `
          + "it may just be slow. Try a narrower command or a smaller test run.]";
      }
      return `${res.output}\n[exit code: ${res.exitCode}]`;
    },
  });
}

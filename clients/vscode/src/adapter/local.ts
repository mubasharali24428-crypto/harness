/**
 * Local execution as the editor's `Execution` (W7 slice 3 adapter).
 *
 * This spawns the **same runner the CLI spawns** — `deploy/runners/local_engine.py`
 * — with the same arguments, so there is one launcher, one `confine` module and
 * one container boundary in the product, not a TypeScript reimplementation of
 * them. It also deliberately mirrors the CLI's local-mode decision: the run is
 * approved on the server, and `start_run` is never called, because the queue
 * would otherwise execute a plan whose work already happened elsewhere.
 *
 * What it does *not* do is apply anything. The launcher copies the run's changed
 * files into a scratch directory (`--work-out`) and this adapter hands them back
 * for cross-checking; writing into the user's workspace stays the controller's
 * reviewed, digest-bound step.
 */

import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { RunError, type Execution, type ExecutionResult } from "../core/run.js";

export interface LocalEngineOptions {
  /** Absolute path to the repo holding `deploy/runners/local_engine.py`. */
  repoRoot: string;
  /** The workspace the run may read and produce changes for. */
  directory: string;
  /** Interpreter with the SDK importable; the CLI resolves the same way. */
  python?: string;
  timeoutSeconds?: number;
  /** Extra environment for the launcher (provider URL and key live here). */
  env?: Record<string, string>;
}

interface RunnerRecord {
  progress?: { kind?: string; text?: string; step?: number };
  refusal?: string;
  engine_result?: Record<string, unknown> & {
    status?: string;
    finalResponse?: string;
    summary?: string;
    local?: { confinement?: string; image?: string; workspace?: string };
    changed?: string[];
    deleted?: string[];
  };
  diff?: string;
  diffOmitted?: string | null;
}

export function localEngineExecution(options: LocalEngineOptions): Execution {
  const runner = resolve(options.repoRoot, "deploy", "runners", "local_engine.py");
  const workDir = mkdtempSync(join(tmpdir(), "harness-ide-out-"));
  const produced = new Map<string, string>();

  return {
    async run({ runId, goal, report }): Promise<ExecutionResult> {
      if (!existsSync(runner)) throw new RunError(`the confined runner is missing: ${runner}`);
      report("executing", `container boundary, workspace ${options.directory}`);

      const argv = [
        runner,
        "--dir",
        options.directory,
        "--goal",
        goal,
        "--run-id",
        runId,
        "--timeout",
        String(options.timeoutSeconds ?? 900),
        "--work-out",
        workDir,
      ];
      const python = options.python ?? "python3";

      const lines = await new Promise<string[]>((resolvePromise, rejectPromise) => {
        const child = spawn(python, argv, {
          cwd: options.repoRoot,
          env: {
            ...process.env,
            // Declared, not defaulted: the runner refuses anything but the
            // container boundary, and an IDE run never gets a weaker one. The
            // refusal stays live — docker, the image and the provider key are
            // still checked before anything is copied.
            HARNESS_ENGINE_CONFINEMENT: "container",
            ...(options.env ?? {}),
          },
        });
        const collected: string[] = [];
        let buffer = "";
        let stderr = "";
        child.stdout.setEncoding("utf8");
        child.stderr.setEncoding("utf8");
        child.stdout.on("data", (chunk: string) => {
          buffer += chunk;
          const parts = buffer.split("\n");
          buffer = parts.pop() ?? "";
          for (const line of parts) {
            if (!line.trim()) continue;
            collected.push(line);
            let record: RunnerRecord;
            try {
              record = JSON.parse(line) as RunnerRecord;
            } catch {
              continue;
            }
            // Forward progress as it arrives: an eight-minute model turn with no
            // signal is indistinguishable from a hang.
            if (record.progress) {
              const text = record.progress.text ?? record.progress.kind ?? "step";
              report("executing", String(text).slice(0, 200));
            }
          }
        });
        child.stderr.on("data", (chunk: string) => {
          stderr += chunk;
        });
        child.on("error", (error) =>
          rejectPromise(new RunError(`launcher failed to start: ${error.message}`)),
        );
        child.on("close", (code) => {
          if (buffer.trim()) collected.push(buffer);
          if (!collected.length) {
            rejectPromise(
              new RunError(
                `the confined launcher produced nothing (exit ${code}): ${stderr.slice(-400) || "(no stderr)"}`,
              ),
            );
            return;
          }
          resolvePromise(collected);
        });
      });

      let final: RunnerRecord | null = null;
      for (const line of lines) {
        let record: RunnerRecord;
        try {
          record = JSON.parse(line) as RunnerRecord;
        } catch {
          continue;
        }
        if (record.refusal) throw new RunError(record.refusal);
        if (record.engine_result) final = record;
      }
      if (!final?.engine_result) {
        throw new RunError("the confined runner produced no result record");
      }
      const result = final.engine_result;
      for (const name of result.changed ?? []) {
        const path = join(workDir, name);
        if (existsSync(path)) produced.set(name, readFileSync(path, "utf8"));
      }
      return {
        state: result.status === "completed" ? "completed" : String(result.status ?? "unknown"),
        summary: String(result.finalResponse ?? result.summary ?? ""),
        diff: final.diff ?? "",
        diffOmitted: final.diffOmitted ?? null,
        confinement: result.local?.confinement,
        image: result.local?.image,
      };
    },

    async readOutput(path: string): Promise<string | null> {
      return produced.get(path) ?? null;
    },
  };
}

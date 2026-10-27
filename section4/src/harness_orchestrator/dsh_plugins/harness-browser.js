// Runtime plugin that adds the `browser` tool (W10), gated by Section 5.
//
// Rendering happens inside the run's own container: every call goes to
// section5/src/harness_sandbox/browser_gate.py, which confines the output to the
// workspace, scrubs the environment (no model key reaches Chromium), sends remote
// URLs through the same web-purpose proxy `web_fetch` uses, and caps the render.
// The engine inserts this file per run (engine._execution_policy_patch); it needs
// no npm install and no Node runtime beyond the one the DSH SDK already runs on.
import { spawn } from "node:child_process";

export const name = "harness-browser";
export const inject = ["tools"];

const DESCRIPTION = `Render a page and look at it: headless Chromium inside this run's sandbox.
* action "screenshot" writes a PNG inside the workspace and returns its path. Open that path with the read tool to actually see the image — the screenshot is evidence only if you look at it.
* action "dump" returns the rendered DOM as text (about 200k characters, truncated).
* url: a workspace-relative page ("index.html", "docs/report.html") or a file:// URL inside the workspace, or an https:// host on the same allow-list web_fetch uses (provider docs and endpoints), through the run's egress proxy. Nothing else loads; a page that tries to fetch an unlisted host will show the failure rather than the content.
* No credentials are given to the browser. If a page needs a session, it will render signed-out.
* A screenshot larger than the byte cap is refused with the size; render smaller with width/height.`;

function runGate(gate, request, signal, proxyUrl) {
  return new Promise((resolve, reject) => {
    // Same rule as web_fetch: the runtime's environment carries the model API
    // key, so the gate gets only what it needs, and the gate strips it again
    // before spawning Chromium.
    const env = { PATH: process.env.PATH ?? "/usr/bin:/bin", HOME: process.env.HOME ?? "/tmp" };
    const proxy = proxyUrl || process.env.HTTPS_PROXY || process.env.https_proxy;
    if (proxy) {
      env.HTTPS_PROXY = proxy;
      env.https_proxy = proxy;
      env.NO_PROXY = "";
      env.no_proxy = "";
    }
    const child = spawn(gate[0], gate.slice(1), {
      env,
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
        reject(new Error(`browser gate failed (exit ${code}): ${err.slice(-500)}`));
      }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

export function apply(ctx, config) {
  ctx.tools.register({
    name: "browser",
    description: DESCRIPTION,
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["screenshot", "dump"],
          description: "screenshot writes a PNG; dump returns the rendered DOM",
        },
        url: { type: "string", description: "file:// URL in the workspace, or an allowed https URL" },
        path: {
          type: "string",
          description: "For screenshot: workspace-relative path ending in .png (e.g. shots/before.png)",
        },
        width: { type: "number", description: "Viewport width in CSS pixels (default 1280)" },
        height: { type: "number", description: "Viewport height in CSS pixels (default 800)" },
      },
      required: ["action", "url"],
    },
    output: { schema: { type: "string" }, render: (_args, value) => [{ type: "text", text: value }] },
    async execute(args, exec) {
      const action = String(args?.action ?? "");
      if (action !== "screenshot" && action !== "dump") {
        throw new Error("action must be 'screenshot' or 'dump'");
      }
      if (typeof args?.url !== "string" || !args.url) throw new Error("url must be a string");
      if (action === "screenshot" && (typeof args?.path !== "string" || !args.path)) {
        throw new Error("path is required for a screenshot (workspace-relative, ends in .png)");
      }
      const res = await runGate(config.gate, {
        action,
        url: args.url,
        path: args.path,
        width: args.width,
        height: args.height,
        workspace: config.workspace,
        timeoutSeconds: config.timeoutSeconds ?? 30,
        maxBytes: config.maxBytes ?? 4_000_000,
      }, exec.signal, config.proxyUrl);
      if (res.denied) throw new Error(`Refused by the browser policy: ${res.denied}`);
      if (res.error) throw new Error(`browser ${action} failed: ${res.error}`);
      if (action === "dump") {
        const truncated = res.truncated ? " (truncated)" : "";
        return `rendered ${res.url}${truncated}\n\n${res.text}`;
      }
      return `rendered ${args.url} → ${res.path} (${res.width}x${res.height}, ${res.bytes} bytes)\n` +
        `Open ${res.path} with the read tool to see the image.`;
    },
  });
}

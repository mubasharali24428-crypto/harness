// Runtime plugin that adds the `web_fetch` tool (W4), gated by Section 5.
//
// Every fetch goes to section5/src/harness_sandbox/web_gate.py: the destination must be
// listed for the "web" purpose (provider docs and provider endpoints, derived from the
// provider registry), https/443 only, and every redirect hop is re-checked. The gate returns
// readable text, never raw HTML, and a refusal is surfaced as the tool's error.
// The engine inserts this file per run (engine._execution_policy_patch); it needs no npm install.
import { spawn } from "node:child_process";

export const name = "harness-web";
export const inject = ["tools"];

const DESCRIPTION = `Fetch one https URL and return its readable text.
* Only allow-listed destinations are reachable: provider documentation and provider API hosts from the harness provider registry. Everything else is refused with a reason.
* https on port 443 only. Redirects are re-checked per hop, so a redirect cannot leave the allow-list.
* The result is truncated (about 40k characters). Fetch a specific documentation page rather than a whole site.
* A refused URL returns the policy reason; pick a different source rather than repeating the call.`;

function runGate(gate, request, signal, proxyUrl) {
  return new Promise((resolve, reject) => {
    // The runtime's environment carries the model API key: the gate gets only what it needs.
    // The run network has no gateway, so the allow-list proxy is the way out; without it the
    // fetch fails closed on name resolution (the denial path never dials and still works).
    const env = { PATH: process.env.PATH ?? "/usr/bin:/bin", HOME: process.env.HOME ?? "/tmp" };
    if (proxyUrl) {
      env.HTTPS_PROXY = proxyUrl;
      env.https_proxy = proxyUrl;
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
        reject(new Error(`web gate failed (exit ${code}): ${err.slice(-500)}`));
      }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

export function apply(ctx, config) {
  ctx.tools.register({
    name: "web_fetch",
    description: DESCRIPTION,
    parameters: {
      type: "object",
      properties: {
        url: { type: "string", description: "Absolute https URL to fetch" },
      },
      required: ["url"],
    },
    output: { schema: { type: "string" }, render: (_args, value) => [{ type: "text", text: value }] },
    async execute(args, exec) {
      if (typeof args?.url !== "string") throw new Error("url must be a string");
      const res = await runGate(config.gate, {
        url: args.url,
        timeoutSeconds: config.timeoutSeconds ?? 15,
        maxBytes: config.maxBytes ?? 2_000_000,
      }, exec.signal, config.proxyUrl);
      if (res.denied) throw new Error(`Denied by the harness egress policy: ${res.denied}`);
      if (res.error) throw new Error(`web_fetch failed: ${res.error}`);
      const truncated = res.truncated ? " (truncated)" : "";
      return `fetched ${res.url} (HTTP ${res.status}, ${res.contentType})${truncated}\n\n${res.text}`;
    },
  });
}

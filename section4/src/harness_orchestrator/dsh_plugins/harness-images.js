// Runtime plugin that adds the `image` tool (W11), gated by Section 5.
//
// Two primitives a model cannot do with its own eyes: compare two renders
// pixel-by-pixel, and read an exact colour. Both go to
// section5/src/harness_sandbox/image_gate.py, which confines every path to this
// run's workspace, caps bytes and pixels, and refuses anything that is not an
// 8-bit PNG. The gate spawns nothing and dials nothing — it reads files.
// The engine inserts this file per run (engine._execution_policy_patch).
import { spawn } from "node:child_process";

export const name = "harness-images";
export const inject = ["tools"];

const DESCRIPTION = `Look at images precisely: pixel diff, pixel sample and palette over PNGs inside this workspace.
* action "diff" compares before/after (same width and height) and returns the changed-pixel count, the ratio, the bounding box, the max channel delta and the mean colour of the changed pixels on each side. It also writes a PNG that paints the changed pixels magenta over a grey copy — open that path with the read tool to see where it differs.
* action "sample" returns the exact rgb/hex colour at up to 64 [x, y] points IN ONE CALL. Use it to read colours off a mockup instead of guessing hex values — and ask for them together: every call costs tool budget.
* action "palette" returns the dominant colours of one PNG in ONE call: each colour's hex, share of the image and bounding box. Start a mockup reproduction here — it answers "what colours, where" in one call — then use sample only for the few points the palette leaves ambiguous. Probing point by point is how a run burns its whole tool budget on colours.
* Use diff after screenshotting your own page against a mockup or a previous render: it is how you know a visual change landed, rather than assuming it did.
* Only 8-bit RGB/RGBA PNGs the size of a screen (capped by bytes and pixels) are compared; anything else is refused with the reason.`;

function runGate(gate, request, signal) {
  return new Promise((resolve, reject) => {
    // Same rule as the browser and web gates: the runtime's environment carries
    // the model API key, so the gate gets only what it needs.
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
        reject(new Error(`image gate failed (exit ${code}): ${err.slice(-500)}`));
      }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

export function apply(ctx, config) {
  ctx.tools.register({
    name: "image",
    description: DESCRIPTION,
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["diff", "sample", "palette"],
          description:
            "diff compares two PNGs; sample reads exact pixels from one; " +
            "palette reports the dominant colours of one PNG and where they are",
        },
        before: { type: "string", description: "For diff: workspace-relative PNG to compare from (e.g. mockup.png)" },
        after: { type: "string", description: "For diff: workspace-relative PNG to compare to (e.g. shots/render.png)" },
        out: { type: "string", description: "For diff: where to write the diff PNG (default <before>.diff.png)" },
        tolerance: {
          type: "number",
          description: "For diff: per-channel delta below which pixels count as equal (default 16)",
        },
        path: { type: "string", description: "For sample and palette: workspace-relative PNG" },
        points: {
          type: "array",
          description: "For sample: [x, y] pixel coordinates, at most 64",
          items: { type: "array", items: { type: "number" } },
        },
        maxColors: {
          type: "number",
          description: "For palette: how many dominant colours to return (default 12, max 24)",
        },
      },
      required: ["action"],
    },
    output: { schema: { type: "string" }, render: (_args, value) => [{ type: "text", text: value }] },
    async execute(args, exec) {
      const action = String(args?.action ?? "");
      if (action !== "diff" && action !== "sample" && action !== "palette") {
        throw new Error("action must be 'diff', 'sample' or 'palette'");
      }
      const request = {
        action,
        workspace: config.workspace,
        maxBytes: config.maxBytes ?? 10_000_000,
        maxPixels: config.maxPixels ?? 4_000_000,
      };
      if (action === "diff") {
        if (typeof args?.before !== "string" || !args.before) throw new Error("before is required");
        if (typeof args?.after !== "string" || !args.after) throw new Error("after is required");
        request.before = args.before;
        request.after = args.after;
        if (typeof args.out === "string" && args.out) request.out = args.out;
        if (typeof args.tolerance === "number") request.tolerance = Math.trunc(args.tolerance);
      } else if (action === "palette") {
        if (typeof args?.path !== "string" || !args.path) throw new Error("path is required");
        request.path = args.path;
        if (typeof args.maxColors === "number") request.maxColors = Math.trunc(args.maxColors);
      } else {
        if (typeof args?.path !== "string" || !args.path) throw new Error("path is required");
        if (!Array.isArray(args.points) || args.points.length === 0) {
          throw new Error("points is required: a list of [x, y] pairs");
        }
        request.path = args.path;
        request.points = args.points.map((p) => (Array.isArray(p) ? [Math.trunc(p[0]), Math.trunc(p[1])] : p));
      }
      const res = await runGate(config.gate, request, exec.signal);
      if (res.denied) throw new Error(`Refused by the image policy: ${res.denied}`);
      if (res.error) throw new Error(`image ${action} failed: ${res.error}`);
      if (action === "sample") {
        const lines = res.points
          .map((p) => `(${p.x}, ${p.y}) rgb(${p.rgb.join(", ")}) ${p.hex}`)
          .join("\n");
        // A model that samples one pixel per call burns its whole budget on
        // colours. The tool can take 64 at once; say so at the moment it matters.
        return res.points.length === 1
          ? `${lines}\n(one point per call — pass up to 64 in a single call)`
          : lines;
      }
      if (action === "palette") {
        const lines = res.colors
          .map((c) => `${c.hex} rgb(${c.rgb.join(", ")}) ${(c.share * 100).toFixed(1)}% ` +
            `of ${res.width}x${res.height}, bbox ${JSON.stringify(c.bbox)}`)
          .join("\n");
        return (
          `palette of ${res.path} (${res.width}x${res.height}, ` +
          `${res.distinctBuckets} colour groups)\n${lines}\n` +
          "sample exact points only to disambiguate what the palette leaves open."
        );
      }
      const box = res.bbox ? `bbox ${JSON.stringify(res.bbox)}` : "no changed region";
      return (
        `diff ${res.before} vs ${res.after} (${res.width}x${res.height}, tolerance ${res.tolerance})\n` +
        `changed ${res.changedPixels}/${res.totalPixels} pixels = ${(res.ratio * 100).toFixed(3)}%, ` +
        `maxDelta ${res.maxDelta}, ${box}\n` +
        (res.changedPixels
          ? `mean colour of changed pixels — before rgb(${res.changedMeanBefore.join(", ")}) ` +
            `vs after rgb(${res.changedMeanAfter.join(", ")})\n`
          : "") +
        `diff image: ${res.out} — open it with the read tool to see where it differs.`
      );
    },
  });
}

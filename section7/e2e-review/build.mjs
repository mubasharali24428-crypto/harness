import { build } from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(here, ".dist");
mkdirSync(out, { recursive: true });
await build({
  entryPoints: { bundle: path.join(here, "harness.tsx"), inbox: path.join(here, "inbox-harness.tsx") },
  bundle: true,
  outdir: out,
  jsx: "automatic",
  alias: { "@": path.join(here, "../src"), "@contracts": path.join(here, "../../contracts/ts/src") },
  define: { "process.env.NODE_ENV": '"production"', process: '{"env":{}}' },
  logLevel: "warning",
});
writeFileSync(
  path.join(out, "inbox.html"),
  `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>inbox</title><style>body{font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:16px;margin:0}button{background:#21262d;color:inherit;border:1px solid #30363d;border-radius:6px}a{color:#58a6ff}</style></head><body><div id="root"></div><script src="inbox.js"></script></body></html>`,
);
writeFileSync(
  path.join(out, "index.html"),
  `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>review</title><style>body{font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:16px}button{margin-right:6px}[aria-pressed=true]{outline:2px solid #3fb950}</style></head><body><div id="root"></div><script src="bundle.js"></script></body></html>`,
);

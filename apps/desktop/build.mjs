import { build } from "esbuild";
for (const [entry, out] of [["src/main.ts", "dist/main.js"], ["src/preload.ts", "dist/preload.js"]]) {
  await build({ entryPoints: [entry], bundle: true, platform: "node", format: "cjs", target: "node20",
    outfile: out, external: ["electron"], logLevel: "warning" });
}

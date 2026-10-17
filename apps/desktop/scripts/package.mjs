// Packages the already-built shell into an unsigned local .app (no code-signing/notarisation:
// distribution to other machines is out of scope and listed as an external validation item).
import packager from "@electron/packager";
import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
const stage = "dist-stage";
rmSync(stage, { recursive: true, force: true });
mkdirSync(stage + "/dist", { recursive: true });
cpSync("dist/main.js", stage + "/dist/main.js");
cpSync("dist/preload.js", stage + "/dist/preload.js");
writeFileSync(stage + "/package.json", JSON.stringify({ name: "harness-desktop", version: "0.1.0", main: "dist/main.js" }));
const out = await packager({ dir: stage, out: "dist-app", name: "Harness", overwrite: true, platform: process.platform, arch: process.arch,
  protocols: [{ name: "Harness", schemes: ["harness"] }], prune: false });
console.log("packaged:", out.join(", "));

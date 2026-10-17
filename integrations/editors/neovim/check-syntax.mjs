// Syntax-only validation of every Lua file (no Neovim/Lua interpreter is available here).
import luaparse from "luaparse";
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
const walk = (d) => readdirSync(d).flatMap((f) => {
  if (f === "node_modules") return [];
  const p = path.join(d, f);
  return statSync(p).isDirectory() ? walk(p) : p.endsWith(".lua") ? [p] : [];
});
let bad = 0;
for (const f of walk(".")) {
  try { luaparse.parse(readFileSync(f, "utf8"), { luaVersion: "5.1" }); console.log("ok  ", f); }
  catch (e) { bad++; console.error("FAIL", f, e.message); }
}
process.exit(bad ? 1 : 0);

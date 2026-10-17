// Loads the REAL packaged bundle (dist/extension.js) with a stub `vscode` module and activates it.
// This is a load smoke, not a VS Code host run (no VS Code exists in this environment).
import Module from "node:module";
import { createRequire } from "node:module";
const registered = [];
const stub = {
  commands: { registerCommand: (id) => (registered.push(id), { dispose() {} }) },
  window: { createOutputChannel: () => ({ appendLine() {} }) },
  workspace: { getConfiguration: () => ({ get: () => undefined }) },
};
const orig = Module._load;
Module._load = function (request, ...rest) {
  return request === "vscode" ? stub : orig.call(this, request, ...rest);
};
const ext = createRequire(import.meta.url)("../dist/extension.js");
ext.activate({ subscriptions: [], secrets: {}, globalState: {} });
const want = ["approvePlan", "reviewChangeSets", "runStatus", "selectProject", "signIn", "signOut"].map((c) => `harness.${c}`);
if (JSON.stringify([...registered].sort()) !== JSON.stringify(want)) {
  console.error("unexpected commands", registered);
  process.exit(1);
}
console.log(`bundle smoke ok: ${registered.length} commands registered`);

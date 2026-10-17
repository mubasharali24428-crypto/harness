// Minimal in-memory stand-in for the `vscode` module: records commands, UI calls and storage
// so extension.ts is exercised through its real activate()/command handlers.
export const state = {
  commands: new Map<string, () => Promise<void>>(),
  infos: [] as string[],
  errors: [] as string[],
  warnings: [] as { msg: string; opts: unknown; items: string[] }[],
  output: [] as string[],
  inputs: [] as (string | undefined)[],
  picks: [] as unknown[],
  warnAnswer: undefined as string | undefined,
  config: {} as Record<string, string>,
};
export function reset() {
  state.commands.clear(); state.infos = []; state.errors = []; state.warnings = [];
  state.output = []; state.inputs = []; state.picks = []; state.warnAnswer = undefined; state.config = {};
}
export const commands = {
  registerCommand: (id: string, fn: () => Promise<void>) => {
    state.commands.set(id, fn);
    return { dispose() {} };
  },
};
export const window = {
  createOutputChannel: () => ({ appendLine: (l: string) => state.output.push(l) }),
  showInputBox: async () => state.inputs.shift(),
  showQuickPick: async (items: unknown[]) => {
    const next = state.picks.shift();
    return typeof next === "function" ? (next as (i: unknown[]) => unknown)(items) : next;
  },
  showInformationMessage: async (m: string) => { state.infos.push(m); },
  showErrorMessage: async (m: string) => { state.errors.push(m); },
  showWarningMessage: async (msg: string, opts: unknown, ...items: string[]) => {
    state.warnings.push({ msg, opts, items });
    return state.warnAnswer;
  },
};
export const workspace = {
  getConfiguration: () => ({ get: (k: string) => state.config[k] }),
};

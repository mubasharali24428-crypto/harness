# W7 `.vsix` package and real-window smoke (2026-10-30)

- Built: `cd clients/vscode && npm run package` -> `harness-vscode.vsix` (11 files, 46 KB; esbuild bundle of `src/extension.ts` with `harness-ts-sdk` inlined, `vscode` external; `@vscode/vsce@3.2.1`). The `.vsix` is git-ignored.
- Installed with `code --install-extension` into a throwaway profile (`--user-data-dir /tmp/hvd`, `--extensions-dir` in scratch); the user's own VS Code profile was not touched.
- Opened a real VS Code 1.140.0 window (arm64) on a scratch folder. Extension host log lines, verbatim:
```
2026-10-30 22:48:00.411 [info] ExtensionService#_doActivateExtension harness.harness-vscode, startup: false, activationEvent: 'onStartupFinished'
2026-10-30 22:48:00.411 [trace] ExtensionService#loadModule [esm] -> file:///private/tmp/claude-501/-Users-user-untitled-folder-2/e7ef1875-a3f2-47fa-874d-1939b259377b/scratchpad/vsc/ext/harness.harness-vscode-0.1.0/dist/extension.js
2026-10-30 22:48:00.412 [trace] ExtensionService#loadExtensionContext harness.harness-vscode
2026-10-30 22:48:00.426 [trace] ExtensionService#_callActivateOptional harness.harness-vscode
2026-10-30 22:48:00.427 [trace] ExtHostCommands#registerCommand harness.login
2026-10-30 22:48:00.427 [trace] ExtHostCommands#registerCommand harness.logout
2026-10-30 22:48:00.427 [trace] ExtHostCommands#registerCommand harness.run
2026-10-30 22:48:00.427 [trace] ExtHostCommands#registerCommand harness.status
```

Result: **activated** (`onStartupFinished`), loaded as ESM, registered `harness.login`, `harness.logout`, `harness.run`, `harness.status`; no error lines in the extension host log.

Not done: the "one task end to end" exit item. It needs typing into the window and a live model run; the computer-use tier for editors is click-only and the shell cannot take screenshots. No screenshot exists. The pre-existing 11/11 live drill still covers the same core code path.

Gotcha: macOS limits unix socket paths to ~104 chars; a long `--user-data-dir` makes VS Code die at startup with `listen EINVAL ...1.14-main.sock`.

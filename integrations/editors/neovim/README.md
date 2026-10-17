# Harness for Neovim (thin client, Neovim >= 0.10)

`:HarnessSignIn [token]` (or `$HARNESS_TOKEN`; held in memory only), `:HarnessProject`, `:HarnessChangeSets`, `:HarnessApprove <runId> <planId>` (explicit confirm).
`require("harness").setup({ identity_url=…, workflow_url=…, orchestrator_url=… })`. https required except loopback. The token is passed to curl through a stdin config, never argv.
**Host validation status:** syntax-checked only in the build environment (no Neovim available); `spec/core_spec.lua` is written but unrun.

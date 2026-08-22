# First-Run Security Checklist — read before connecting a private repository

- [ ] **Sandbox scope**: agents run inside ephemeral containers. Commands are
      allow-listed (tests, linters, builds, lockfile installs). Deletes, force-push,
      secret reads, package publishes, and infra tools are blocked in code.
- [ ] **Network egress**: sandbox containers may reach only pinned registries
      (pypi.org, npmjs.com) and your repo's own git remote. Nothing else.
- [ ] **Secrets**: your API keys are encrypted at rest and decrypted only inside the
      model-router service. Secrets never enter model context or logs. Never paste
      secrets into chat or commit them to the demo repo.
- [ ] **Forbidden paths**: plans carry `forbidden_paths` (default: `.env*`, `infra/`).
      Agents cannot touch those paths — enforced in code, not policy documents.
- [ ] **Merges are manual**. Always. This app never merges a PR; humans merge on
      GitHub after review. There is no auto-merge anywhere in the system.
- [ ] **Plan gate**: no code executes until a human approves the agent's plan.
      No exceptions, including "quick fixes".
- [ ] **Audit trail**: plan approvals, pauses, takeovers, review decisions, and
      merges are appended to an append-only audit log.

# Onboarding order

1. Open http://localhost:3000
2. Log in (email creates your solo team; you are admin)
3. Connect GitHub (Settings → OAuth), approve the `repo` + `read:user` scopes
4. Create a project pointing at `harness-demo/textstats` (the seeded demo repo)
   — or your own private repo once you've read this checklist
5. Invite teammates (admin → invite by email); add agent seats if wanted
6. Start a Meeting; type `@ai turn this into a brief`; review + APPROVE the brief
7. Follow the guided demo: brief → plan proposal → approve → sandboxed run on its
   own branch → pause/edit → done-signal → reviewer pass → draft PR → Review Area
8. Approve in Review Area, then merge manually on GitHub

Telemetry: error logs and usage counts are recorded locally by default. Full prompt
text and code content are collected only if you set `HARNESS_TELEMETRY_FULL=1`.

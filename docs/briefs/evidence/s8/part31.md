# Part 31 — data and privacy

## Directive results

| # | Status | Evidence and remaining gap |
|---|---|---|
| 001 | Partial | `docs/data-map.md` catalogs product storage and readers; no central data-minimization review exists for all producers. |
| 002 | Partial | Event schemas and exports use bounded summaries; raw prompts/comments and collaboration guidance remain stored as operational content. |
| 003 | Implemented locally with limits | Session/API keys are hashed or vault-encrypted; audit scrubbing and notifier error handling avoid credential values. Production logging/provider boundaries were not independently inspected. |
| 004 | Implemented locally | Run, comment, plan, artifact and guidance stores are treated as user content; visibility is scoped by auth guards and export filters. |
| 005 | Partial | Audit/export and run-history endpoints have team/run guards; no single concise product-facing access policy lists every data class. |
| 006 | Partial | Event/audit retention sidecar and Section 4 run-retention/deletion workflows exist. Team-admin hold create/read/release operations are audited, and run purge, event/audit pruning and PostgreSQL event-partition retirement preserve held-run evidence. Content deletion and backup expiry honor holds (Session 11). Account erasure pseudonymises the account, refuses while a held run involves the person, and keeps ids in evidence (`test_account_erasure.py`, sqlite only). |
| 007 | Implemented locally with limits | Section 4 exports authorize the requester, enforce size/expiry, redact secret-shaped values and audit operations; local export files depend on operator filesystem controls. |
| 008 | Partial | Demo fixtures are synthetic by construction in the inspected runner; no global product guard prevents a user from supplying real data. |
| 009 | Partial | `docs/data-map.md` describes telemetry fields; no complete participant-facing telemetry notice found. |
| 010 | Partial | No external analytics integration found in inspected paths. Model/provider calls are deliberate product behavior, but a complete provider disclosure is not present. |

## Verification

- Section 5 sandbox command/path enforcement: `tests/unit/test_policy.py`,
  `tests/unit/test_s1_r03_sandbox_policy.py`, `tests/unit/test_sandbox_isolation.py`.
- Section 4 export/deletion: `tests/unit/test_exports_deletion.py`, `tests/unit/test_exports.py`.
- Section 1 secret logging/audit: `tests/test_security_headers.py`, `tests/test_audit_emission.py`.
- Data-flow map: [`docs/data-map.md`](../../../data-map.md).

## Critical limitation

Section 5 enforces its allow-list and `forbidden_paths` when requests enter the Section 5 sandbox
API. Section 4 now pins the SDK `sdk-minimal` profile and appends a mandatory `workspace-write`
policy overlay scoped to that run. Section 4 refuses execution before SDK invocation whenever the
approved plan declares any `forbiddenPaths`; it cannot selectively enforce those paths. The SDK
persistent shell still does not pass commands through Section 5's command allow-list. Part 31 remains
partial until a common hard execution adapter covers that path. The installed SDK API exposes
post-dispatch notifications and collected events, not a pre-tool authorization callback, so the
existing wrapper cannot safely enforce commands by inspecting those notifications. No compliance, legal, production
encryption, or provider disclosure claim is made.

Session 11 live check (Part 52): no local closure. The runtime is a closed binary and the SDK config exposes only provider, model, profile, patches, env, cwd and dsh_home, so there is no pre-tool callback. It spawns `/bin/bash` by absolute path, so a PATH shim never sees the command (shim installed, run driven by a local Ollama model: the command ran unobserved). Seatbelt breaks its PTY, and the real runtime accepts only `container` or an explicit `off`. The remaining closure paths are a runtime pre-tool hook or the image-level boundary in `worker.Dockerfile` (container work in another lane).

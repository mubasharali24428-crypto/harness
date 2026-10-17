# A4-S4 — Intake breadth, guided onboarding and the task CLI

Baseline 31bf407, branch `codex/harness-a4`. Nothing pushed.

## Built
- F039: Bitbucket Cloud and Azure DevOps intake in `section2/.../intake/` (signing, triggers, guarded Bitbucket client).
- F131–F134 in `integrations/editors/service/.../guided.py` + routes: onboarding (derived), readiness, tutorial project, task templates.
- F005: `section7` `TaskInbox` + `intakeApi`, real-browser phone test.
- VR003: `packages/dev-cli` (`harness-task`), zero dependencies.

## Decisions worth knowing
- Onboarding steps are derived from stored activity, never ticked by the user.
- Only the service-owned `data_dir/tutorial` root is added to allowed repo roots; operator roots stay fail-closed.
- Azure service hooks cannot sign bodies, so the control is a shared secret (header or Basic): recorded as weaker than HMAC.
- Intake text is neutralised before storage and rendered as text in the UI; `javascript:` links are dropped.

## Results (`evidence/`)
section2 92 pass; editor service 47; section7 529 vitest + tsc; Playwright 6 (2 new mobile, real section2); dev-cli 6 (real service); ruff clean on touched files.

## Honest limits
- Phone layout verified in desktop Chromium emulation only. Not a real device, not a native app.
- Bitbucket/Azure payloads follow documentation and were not captured from live providers; Azure has no write-back.
- Approval still dispatches nothing (A2 integration). CLI is not packaged or published.

# Marathon 2.0 W20 — release readiness and handoff (M2-W20)

The 2026-10-28 Marathon 2.0 roadmap reserves W20 for an independent rerun of the
held-out task suite, the live journey and the release checklist, plus a versioned
evidence pack, the outstanding limitations and the next roadmap. Its exit is a
decision-ready evidence pack and a pilot/full-release decision **recorded by an
authorized owner**; if gates fail, release is deferred and the plan is redone.

This document is the W20 record. It is **not** a release decision. `STATUS.md`
still says NOT READY for production or a general pilot, and nothing here changes it.

## Outcome (2026-10-30)

The rerun ran. The evidence pack is in
[`docs/briefs/evidence/w20/`](briefs/evidence/w20/README.md), with every raw
file, a hash manifest and the measured spend.

**The held-out gate fails.** Four of six held-out tasks pass every run; the two
vision tasks (`w11-vision`, `w14-design-heldout`) pass **0 of 3** each. The
cause is the deployed hosted model (`qwen/qwen3.7-flash` on OpenRouter)
returning empty responses mid-run; in every case the engine stopped the run as
`blocked` rather than report success, so the safety behaviour held and the
capability did not. Under the roadmap's rule, a failed gate means the release is
deferred and replanned — see "Recommendation" below. The decision record stays
blank for the authorized owner.

Everything else that W20 could run passed at one verified revision:

| Area | Result |
|---|---|
| Revision | `run_eval.py --verify-only` `verified: true` for orchestrator, worker image **and** worker daemon at HEAD `12fb221`; a clean build of HEAD's Dockerfile matches the deployed runtime files byte for byte (108/108) |
| Fixed set, no model | **15/15 PASS** (boundaries, egress, quotas, LSP confinement, skills, memory, BYO runner, browser session, visuals, journey) |
| Held-out, deterministic | `stale-edit` PASS, `tenant-isolation-api` PASS (36 run routes + 3 project routes, no existence oracle, no leak) |
| Held-out, live (3 independent runs each) | `w8-crash-cleanup` 3/3, `code-search-heldout` 3/3, `w11-vision` 0/3, `w14-design-heldout` 0/3 |
| New agent tasks (3 runs each) | `tests-written` 3/3; `multi-file-refactor` 1/3 (code right every time; twice left the old name in `README.md`) |
| `w11-grounding` (3 runs) | 3/3 |
| Live journey | PASS on the verified stack |
| Local gates | `make static-gates`, `make lint`, pytest in every section, MCP, Section 7 `tsc`/`vitest`/`next build`: all pass |
| Spend | about **$0.044** measured for 21 live runs (ledger delta $0.036632 + $0.007255 reported directly) |

## What W20 found and fixed

1. **Agent runs could not dispatch.** The worker daemon (compose profile
   `worker`) had been left behind when the backend was recreated: old image, and a
   `HARNESS_WORKER_DAEMON_TOKEN` that no longer matched the orchestrator's, so it
   answered every dispatch `401`. Recreated from the root `.env`; runbook
   [`ops.md` §3.11](runbooks/ops.md).
2. **The revision check had a blind spot.** `--verify-only` checked the
   orchestrator and worker image but not the daemon, which imports `engine.py` and
   launches every worker. It now checks the daemon too; the first run of the new
   check failed on the stale daemon.
3. **The worker image was 12 files behind HEAD** and missing
   `harness_contracts/rate_card.py`. Refreshed by re-running the Dockerfile's own
   pip step from `git archive HEAD`; a later clean build confirmed the same bytes.
   Rollback tags `harness-engine-worker:pre-w20-overlay` and
   `harness-worker-daemon:pre-w20-overlay` are kept.
4. **`--repeats N` is retry-until-first-pass**, not N independent runs, so it
   cannot support a rate. Live tasks ran as three separate invocations;
   `PROTOCOL.md` and the help text now say so.
5. **Back-to-back drills trip the login limit** (about 5 logins a minute per
   IP): three tasks failed in under a second with `429`. The runner gained
   `--gap SECONDS`; `PROTOCOL.md` documents `--gap 65`.
6. **Corpus fixes.** `w11-grounding` was labelled `infra` but calls a vision
   model (reclassified `live`) and needs the repo source on `PYTHONPATH` (set in
   its `driver.env`). The new `tenant-isolation-api` driver's first run exposed
   its own defect (a `GET /projects/{id}` route that does not exist); its project
   half now comes from the identity spec.
7. **`agent_events` partitions exist through 2026-12-31**, so the 2026-11-01
   write cliff is covered.

Two observations for other owners, recorded and not changed here:

* **Possible lease weakness (W17).** One `w14-design-heldout` run hit
  `heartbeat_lost` after a ~6-minute vision step; the orchestrator later logged
  "claim lost to another worker; fencing local engine".
* **Port interference on this machine.** One `w11-vision` run's login was
  answered by a Django development page on `127.0.0.1:8001`: another application
  briefly bound the identity port. That run is recorded as invalid, not as a
  product or model result.

## Coordination

W17 was executed concurrently in this tree by another agent. W20 did not edit,
stage or commit its files (`eventbus.py`, `run_probe.sh`, `pitr_drill.sh`, the
W17 plan, runbook, tests and evidence). W20 did change the shared stack in two
reversible ways: it retagged the worker and daemon images and recreated the
`worker-daemon` container (no other service), with rollback tags kept. No run
was in flight at either change.

## Release checklist

A ticked box means the evidence exists at the verified revision, **not** that
the gate passed; the result is stated on the line.

- [x] Images verified against HEAD by hash, daemon included (`verified: true`); clean build matches
- [x] Fixed set rerun: 15/15 PASS. Deferred with reason: six tasks pinned to a local chat model that does not exist on this machine (`m03-scope`, `m04-streaming`, `m07-api-run`, `m07-daemon-topology`, `w3-rewind`, `w3-partial-write`; owner: evaluation, decide model or retire) and `native-tool-confinement` (M01)
- [x] Held-out set rerun, live tasks 3 independent runs with the pinned model — **result: gate FAILS** (both vision tasks 0/3)
- [ ] M01 native-tool confinement closed — a 1-2 day security spike across every enforcement backend; not attempted in W20
- [x] Compose live journey passes on the verified stack
- [x] `make static-gates`, every section's pytest and Ruff, Section 7 typecheck/tests/build pass
- [x] No unsafe action: every boundary task passed; every live failure stopped as `blocked`/refused rather than reporting success
- [ ] W17 owner's SLOs defined and measured; capacity envelope stated with its limits
- [ ] Restore drill on the real deployment; scheduled-restore trigger observed
- [ ] Backup monitoring and on-call/status ownership assigned to named people
- [x] W16 documentation claims match the code (key and residency management) — static audit, see `docs/m2-w16-plan.md`
- [ ] W13 execution leg proven with a real bring-your-own runner (a product feature, not built)
- [ ] W15 scheduled restore observed running (next run 2026-11-01T12:00Z; the earlier trigger hit an account credit limit)
- [x] `agent_events` partitions present through 2026-12-31 (checked 2026-10-30)
- [ ] Package publish (PyPI/npm) credentials and the publish step decided by their owner
- [ ] Pricing amounts and invoicing terms approved by counsel
- [ ] SSO/OIDC (risk register R-45): shipped and tested; the owner's pilot need-by decision remains
- [ ] W18 release-candidate review finished by someone other than the author
- [ ] External security review scheduled or done
- [ ] Hermes rights evidence in hand, or the launch claim narrowed to exclude Hermes
- [ ] No auto-merge; manual plan approval and rollback documented and drilled at this revision
- [ ] Every removed item recorded with an owner and a follow-up

## Recommendation (for the owner; not a decision)

* **Defer full release.** The held-out vision gate fails on the deployed model.
* **Replan vision first:** choose a vision model or provider that completes the
  W11/W14 tasks, redeploy, and rerun only those two tasks three times. If they pass,
  re-run the whole held-out set once at that revision.
* **A design-partner pilot that excludes vision features** is the narrower claim
  this evidence can support, subject to the people-owned items above (security
  review, counsel, named on-call). That choice belongs to the authorized owner.

## Gate ledger, W1-W19, as recorded in each plan

The rerun re-proved the rows marked *re-run 2026-10-30*; others stand as each
plan records them.

| Week | Recorded position | Open item carried forward |
|---|---|---|
| W1 baseline | partial baseline; *re-run 2026-10-30 at a verified revision* | M01 open; six tasks need a local chat model |
| W2 code intelligence | *re-run: `code-search-heldout` 3/3* | one synthetic fixture |
| W3 safe edits | stale patch, rewind, scope, denials; *re-run: `stale-edit` PASS* | worker/child stop and partial-write reconcile still historical only |
| W4 SDK/boundary | *re-run: egress, AST/LSP confinement PASS* | no real third-party MCP; package awareness not claimed |
| W5 CLI | *re-run: `cli-diagnostics` PASS* | none recorded as blocking |
| W6 skills | *re-run: `skills-governance` PASS* | Hermes H0 paperwork absent |
| W7 LSP/editor | *re-run: `lsp-real-server` PASS* | `.vsix` and real-window smoke not run; Hermes H3 blocked |
| W8 parallel isolation | *re-run: quotas, apply-conflict, crash cleanup 3/3 PASS* | universal isolation not claimed |
| W9 artifacts | *re-run: `w9-visuals` PASS* | none recorded as blocking |
| W10 browser | *re-run: `w10-browser-session` PASS* | remote subresources rely on the proxy |
| W11 vision | *re-run: `w11-grounding` 3/3; `w11-vision` 0/3* | **vision task fails on the deployed model** |
| W12 skills/memory | *re-run: `w12-skills-memory` PASS* | none recorded as blocking |
| W13 BYO runners | *re-run: `w13-byo-runner` PASS* | execution leg not built |
| W14 model intelligence | *re-run: `w14-design-heldout` 0/3* | **vision held-out fails on the deployed model** |
| W15 multi-tenant | fairness, per-team limits, key rotation | scheduled restore trigger never observed |
| W16 enterprise | shipped; docs-vs-code audit closed | CMEK and residency enforcement absent by design |
| W17 reliability | in progress, other agent | no SLO met or owned; possible lease weakness seen in W20 |
| W18 launch | slices committed | independent review unfinished |
| W19 buffer | open-gates table committed | its items remain open |

## Rerun procedure (to repeat W20)

1. Record `git status`; the runtime source dirs must be clean.
2. If the backend was recreated, recreate `worker-daemon` too (`ops.md` §3.11).
   `run_eval.py --verify-only` must print `verified: true` before any number is quoted.
3. `make static-gates`, `make lint`, every section's pytest, `make frontend`, and
   `npm run build` in `section7`.
4. `run_eval.py --cost infra --gap 65` for the fixed and deterministic held-out set.
5. Each live task three times, one `--task` per invocation, 65 s apart; snapshot
   `cost_ledger` before and after.
6. Fill the checklist from the raw files; an owner who did not write the code
   reviews the pack and records the decision.

## Outstanding limitations

* Vision capability depends on a model that currently fails the held-out tasks.
* Nothing here measures real-agent capacity or SLO compliance (W17).
* n=3 per live task is enough to see a pattern, not to quote a precise rate.
* The deployed images are overlays; a clean build was verified byte-identical but
  is not what is running.
* Stripe is undrilled by decision; billing stays flag-off.
* Index admission exclusion is per process, not across replicas.
* M01 native-tool confinement is open; no access is widened until it closes.

## Decision record

| Field | Value |
|---|---|
| Revision / image digests | HEAD `12fb221`; worker `sha256:a5f587aeb1f0…`, daemon `sha256:00c0b7107bee…`, backend `sha256:5973222fefd5…` |
| Evidence pack manifest | `docs/briefs/evidence/w20/README.md` |
| Reviewer (not the author) | _not set_ |
| Decision (pilot / full release / defer and replan) | _not set — an authorized owner records this_ |
| Date | _not set_ |

# Marathon 2.0 W20 — release readiness and handoff (M2-W20)

The 2026-10-28 Marathon 2.0 roadmap reserves W20 for an independent rerun of the
held-out task suite, the live journey and the release checklist, plus a versioned
evidence pack, the outstanding limitations and the next roadmap. Its exit is a
decision-ready evidence pack and a pilot/full-release decision **recorded by an
authorized owner**; if gates fail, release is deferred and the plan is redone.

This document is the W20 working record. It is **not** a release decision, a
readiness verdict, or a claim that any W1-W19 gate holds at HEAD. `STATUS.md`
still says NOT READY for production or a general pilot, and nothing here changes it.

## Coordination (2026-10-30)

W17 is being executed concurrently in this tree by another agent. W20 was written
to avoid every file and resource that agent holds:

| Held by the W17 agent (not touched by W20) | Why it matters |
|---|---|
| `section1/src/harness_identity/eventbus.py`, `scripts/loadtest/run_probe.sh`, `scripts/pitr_drill.sh` (modified, uncommitted) | source W17 is changing |
| `docs/m2-w17-plan.md`, `docs/runbooks/w17-reliability.md`, `docs/briefs/evidence/w17/`, `section1/tests/test_w17_*.py` (untracked) | W17's record and evidence |
| the running compose stack (17 containers), its database and its load/PITR drills | a W20 rerun would contend with W17's drills and disturb its measurements |

W20 adds this one file. It makes no commit, restarts nothing, and runs no live
task. Everything it ran was read-only: the corpus contract check, the deployed-vs-
worktree hash check, and a `--dry-run` of the held-out selection.

## Starting audit (2026-10-30)

| W20 requirement | What exists | Gap found |
|---|---|---|
| Independent rerun of the held-out suite | corpus of 39 tasks, 6 held out, 29 implemented; `scripts/eval/run_eval.py` | see "Why the reruns have not been run" |
| Live journey | `scripts/e2e/compose_journey.py` (12 checks); W18 live demo | would contend with W17 on the shared stack |
| Release checklist | none as a single document; per-week "gate position" tables in `docs/m2-w*-plan.md` | assembled below, every box unchecked |
| Versioned evidence pack | evidence is spread over `docs/briefs/evidence/` (eval, w17, w19 files) | indexed below; pack is not frozen |
| Outstanding limitations | each plan has a Limitations section | consolidated below |
| Next roadmap | none after W20 | proposal below |
| Authorized decision | none | decision record left blank |

### Checks run today (read-only)

* `run_eval.py --check`: `ok`, 39 tasks, 6 held out, 29 implemented. This validates
  the corpus contract only; it executes nothing.
* `run_eval.py --verify-only`: **`verified: false`**. Backend files and two worker
  files match the worktree; the worker image's `engine.py` does not (host
  `2bca7251…`, deployed `375b2769…`). Under the protocol, any result produced now
  is `stale_image: true` and cannot be promoted. This is the same drift W19
  recorded; it has not been rebuilt.
* `--dry-run` of each held-out id with `--include-live`: five are selectable
  (`w8-crash-cleanup`, `w11-vision`, `stale-edit`, `code-search-heldout`,
  `w14-design-heldout`); **`tenant-isolation-api` selects nothing** because its
  status is `fixture-owed`. A held-out task that cannot run is a gap, not a pass.

## Why the reruns have not been run

All of these are open; none is waived.

1. **Revision drift.** The deployed worker's `engine.py` differs from the worktree.
   Rebuild and re-verify at a coordinated point first, or the whole rerun is
   unpromotable.
2. **Shared stack.** The live journey and `w8-crash-cleanup` act on the stack the W17
   agent is measuring. Run them after W17's owner finishes, or on a separately
   identified environment.
3. **Model spend.** `w11-vision`, `code-search-heldout`, `w14-design-heldout` and
   `w8-crash-cleanup` are `cost: live`. The protocol asks for at least 3 repeats
   for any rate claim. A budget and a pinned model must be agreed by the owner.
4. **No local chat model.** Six fixed-set tasks (`m03-scope`, `m04-streaming`,
   `m07-api-run`, `m07-daemon-topology`, `w3-rewind`, `w3-partial-write`) are
   `blocked`: Ollama holds only `nomic-embed-text`. They stay blocked until a chat
   model is pulled or the drills are pointed at a funded provider.
5. **Fixtures owed.** `tenant-isolation-api` (held out), `multi-file-refactor`,
   `tests-written` and `native-tool-confinement` (M01, still open).

## Rerun procedure (for whoever runs it, once the preconditions hold)

1. Freeze HEAD; record `git status` and confirm the worktree is clean.
2. Rebuild backend and worker images from HEAD. `run_eval.py --verify-only` must
   print `verified: true` before any number is quoted.
3. `run_eval.py --check`, then `make static-gates`, the Section 1 pytest and Ruff,
   and the Section 7 typecheck, unit tests and build.
4. Held-out set, one task per call, `--include-live --repeats 3`, with the pinned
   model recorded: `w8-crash-cleanup`, `w11-vision`, `stale-edit`,
   `code-search-heldout`, `w14-design-heldout`. Keep failures; do not rerun until
   green without keeping the earlier file.
5. `scripts/e2e/compose_journey.py` on the verified stack, plus the W18 live demo.
6. Fill the checklist below from the raw evidence files, not from the plans.
7. An owner who did not write the code reviews the pack and records the decision.

## Gate ledger, W1-W19, as recorded in each plan

"Recorded" means the plan's own gate table. W20 has not re-verified any row at
HEAD; the rerun above does that.

| Week | Recorded position | Open item carried into W20 |
|---|---|---|
| W1 baseline | partial baseline passed (3/3 executed, 22 deferred) | full set never executed; M01 open |
| W2 code intelligence | bounded search task passed | one synthetic fixture, n=1 |
| W3 safe edits | stale patch, rewind, scope, denials closed | worker/child stop and partial-write reconcile are historical only (2026-10-26) |
| W4 SDK/boundary | web, install, egress, MCP closed; AST/LSP spike closed | no real third-party MCP; package awareness not claimed |
| W5 CLI | parity, token handling, reconnect, capture closed | none recorded as blocking |
| W6 skills | prototype closed | Hermes H0 paperwork absent; H2 not attempted |
| W7 LSP/editor | 16-check LSP drill, editor round-trip passed | `.vsix` and real-window smoke not run; Hermes H3 blocked |
| W8 parallel isolation | three concurrent runs, cleanup, hash-anchored apply | universal isolation not claimed (one host, n=1); engine does not call quota endpoints |
| W9 artifacts | shares, revocation, deployment binding, screenshots passed | none recorded as blocking |
| W10 browser | persistent adapter, 19 actions, redirects, downloads, clipboard | remote subresources rely on the proxy |
| W11 vision | click executed then judged, model grounding vs AX | n=1; no broad generalization or accessibility claim |
| W12 skills/memory | catalog, injection handling, project memory passed | none recorded as blocking |
| W13 BYO runners | threat model, lifecycle, revocation, replay, isolation | "Remaining (explicitly open)" section in its plan |
| W14 model intelligence | hold-out enforced, comparisons, opt-in routing, dashboards | n=1 per model; both models failed their own verifier on the compared task |
| W15 multi-tenant | fairness, per-team limits, key rotation | scheduled restore trigger never observed (account credit limit) |
| W16 enterprise | deactivation, group-to-role, SCIM subset, SIEM shipped; docs-vs-code key/residency audit closed 2026-10-30 (grep, no diff needed) | CMEK and residency enforcement remain absent by design; counsel sign-off on terms is a human step |
| W17 reliability | **in progress, other agent**: first slice and an 8/100-user dry-engine run | no SLO met; 100-user control p95 1.4 s, engine-wait p95 27 s; no live-agent capacity; backup-recording warning; no on-call owner |
| W18 launch | slices 1-6 committed (pricing test, importer, docs, demo) | release-candidate independent review unfinished; credential filter is best-effort; GET/PUT can overwrite a concurrent edit |
| W19 buffer | defect fixes and an open-gates table committed | open items in that table remain open |

## Release checklist (all unchecked)

Each box needs raw evidence at one named revision and image digest.

- [ ] Worker and backend images verified against HEAD by hash (`verified: true`)
- [ ] Fixed-set tasks rerun; failures and deferrals listed with reason and owner
- [ ] Held-out set rerun with 3 repeats and pinned model; `tenant-isolation-api` fixture built
- [ ] M01 native-tool confinement closed
- [ ] Compose live journey passes on the verified stack
- [ ] `make static-gates`, Section 1 pytest and Ruff, Section 7 typecheck/tests/build pass
- [ ] No unsafe action in any task; safety failures reported as hard fails
- [ ] W17 owner's SLOs defined and measured; capacity envelope stated with its limits
- [ ] Restore drill on the real deployment; scheduled-restore trigger observed
- [ ] Backup monitoring and on-call/status ownership assigned to named people
- [x] W16 documentation claims match the code (key and residency management) — static audit 2026-10-30, see `docs/m2-w16-plan.md`
- [ ] W13 execution leg proven with a real bring-your-own runner (needs an external runner)
- [ ] W15 scheduled restore observed running (next run 2026-11-01T12:00Z; the earlier trigger hit an account credit limit)
- [ ] `agent_events` partitions confirmed present past 2026-10 (or the `events-retention` sidecar confirmed) before 2026-11-01
- [ ] Package publish (PyPI/npm) credentials and the publish step decided by their owner
- [ ] Pricing amounts and invoicing terms approved by counsel; the page maps only to enforced caps until then
- [ ] SSO/OIDC hardening items (M4) scheduled or closed
- [ ] W18 release-candidate review finished by someone other than the author
- [ ] External security review scheduled or done; counsel review of terms
- [ ] Hermes rights evidence in hand, or the launch claim narrowed to exclude Hermes
- [ ] No auto-merge; manual plan approval and rollback documented and drilled
- [ ] Every removed item recorded with an owner and a follow-up

## Evidence pack (index, not yet frozen)

* Corpus and protocol: `docs/eval/corpus.json`, `docs/eval/PROTOCOL.md`
* Baseline and per-week: `docs/briefs/evidence/eval/`
* W17 (other agent, in progress): `docs/briefs/evidence/w17/`
* W19: `docs/briefs/evidence/w19-skills-eval-verified.json`,
  `w19-boundary-eval.json`, `w19-skills-eval.json`
* Status of record: `STATUS.md` (NOT READY), `docs/pilot-remediation/`

The pack is frozen only at the end of a clean rerun, with the commit, image
digests, model, environment, reviewer and defects named in a manifest.

## Outstanding limitations

* Deployed worker image is not the checked revision.
* Nothing in the 100-user load result predicts real-agent capacity or SLO compliance.
* Held-out sets are small (n=1 for several tasks), so no rate or generalization claim.
* Stripe is undrilled by decision; billing stays flag-off.
* External security review, counsel review and Hermes rights are tracked outside
  this tree and block the commercial and Hermes claims.
* Index admission exclusion is per process, not across replicas.
* Native-tool confinement (M01) is open; no access is widened until it closes.

## Next roadmap (proposal for the owner, not a commitment)

1. Finish W17, then a coordinated image rebuild and `--verify-only` green.
2. Build the missing fixtures (tenant-isolation matrix, refactor, tests-written) and
   close M01.
3. One funded, pinned-model rerun with repeats; this is the real W20 gate.
4. Assign named owners for on-call, status, backups and the release review.
5. Decide the narrowed launch claim (design-partner pilot vs wider) from the rerun.
6. Re-estimate from measured capacity before setting any dates.

## Decision record

| Field | Value |
|---|---|
| Revision / image digests | _not set_ |
| Evidence pack manifest | _not set_ |
| Reviewer (not the author) | _not set_ |
| Decision (pilot / full release / defer and replan) | _not set — an authorized owner records this_ |
| Date | _not set_ |

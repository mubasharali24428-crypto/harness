# W33 readiness pack: W20 checklist refilled from raw evidence (2026-11-05)

**Status: prepared for an independent reviewer. Not reviewed. Not a release decision.**
The author of the code under test assembled it, so by the W20 procedure it carries no
independent weight until someone else signs below.

## Revision under test

* Git HEAD `269ea0b9427615fb52be67fab53eb79746c1fb4c` (branch `w28-s1-delegate`), plus one
  uncommitted-at-run test-only fix (two line-length errors in `section4/tests/unit/test_delegation.py`
  that made `make lint` fail at HEAD; committed with this pack). No runtime source changed.
* `git status`: only `docs/yc/` untracked. `contracts`, `section4`, `section5`, `deploy/patches`,
  `deploy/mcp` clean.
* `run_eval.py --verify-only`: `verified: true` for the orchestrator, worker and daemon (hash of
  baked-in files equals host files).
* **Tags are not uniform:** backend services `vw28s6`; worker and worker-daemon `vw27`; frontend
  `vw24b2` (unchanged for days). The UI is published on host port 3010, not 3000.
* Model: the stack `DSH_MODEL` is `anthropic/claude-haiku-5.5` through OpenRouter.
* Prior caveat still applies: images are compose builds of this tree, but the deployed set was not
  rebuilt from scratch in this pass.

## Raw evidence (all under `docs/briefs/evidence/w33/` unless stated)

| Check | Result | File / log |
|---|---|---|
| `make static-gates` | pass (corpus 39 implemented, 60 env knobs catalogued) | run log, not kept |
| `make lint` | **failed at HEAD** (2 E501 in a test file I wrote); passes after fix | fixed in this commit |
| `make test` (all sections) | pass: 1240+48+149+2146+460+123+91+91+60 passed, 63+59+8 skipped | run log, not kept |
| `make frontend` | pass | run log, not kept |
| `npm run build` in `section7` | pass | run log, not kept |
| Infra eval set | **15/16 first pass.** `stack-compose-journey` FAILED (181 s): journey probed UI at `:3000`, which is not where the UI is published here. Re-run with `E2E_FRONTEND_URL=http://127.0.0.1:3010`: PASS (3.2 s). Both attempts kept. The failure is a configuration default, not a product defect, but **Fixed afterwards:** `compose_journey.py` now takes the port from `HARNESS_FRONTEND_PORT` (environment, else the repo `.env`, else 3000); rerun with no override: PASS 3.1 s (`infra-eval-compose-default.json`). | `infra-eval.json`, `infra-eval-compose-3010.json` |
| Live set, n=1 each, stack model | `multi-file-refactor` PASS 37.1 s, `tests-written` PASS 25.0 s, `w11-grounding` PASS 32.9 s, `code-search-heldout` PASS 21.0 s, `w8-crash-cleanup` PASS 26.9 s, `w11-vision` PASS 50.7 s, `w14-design-heldout` PASS 35.8 s | `live-<task>.json` |
| Live spend for that set | $0.0379 from `cost_ledger` (before/after) | script output |
| Six formerly local tasks, earlier today | `m07-api-run`, `w3-rewind`, `w3-partial-write`, `m03-scope`, `m04-streaming`, `m07-daemon-topology` all PASS on Haiku | `docs/briefs/evidence/eval/`, `pilot-prep/` |

Limits of this run: **n=1 per live task** (W20 used n=3); not repeated 65 s apart; 16 of 23 live
tasks were not rerun (only the fixed set and the held-out live set); the two earlier-known flaky
items remain (`test_reclaimed_run_fences_the_original_worker` fails under full-suite load about 15%
of runs; it passed in this run).

## Checklist, refilled

A ticked box means the evidence exists at this revision, not that a gate passed; the result is on
the line. Unticked means no evidence in hand or the item belongs to a person.

- [x] Images verified against HEAD by hash, daemon included (`verified: true`).
- [x] Fixed live set (`multi-file-refactor`, `tests-written`, `w11-grounding`) rerun on Haiku: 3/3 PASS, n=1 (was unticked in W20).
- [x] Held-out set: live tasks 4/4 PASS n=1 (`code-search-heldout`, `w8-crash-cleanup`, `w11-vision`, `w14-design-heldout`); deterministic held-out (`stale-edit`, `tenant-isolation-api`) PASS in the infra set.
- [x] Six locally pinned tasks decided: repinned to Haiku, all PASS (2026-11-05).
- [x] Compose live journey: PASS with the correct UI port; one FAIL kept (wrong default port).
- [x] `make static-gates`, `make lint` (after the fix), every section's pytest, frontend, Section 7 build.
- [x] No unsafe action: boundary and confinement tasks in the infra set passed (`native-tool-confinement`, `ast-lsp-confinement`, `w4-egress-deny`, quotas, tenant isolation).
- [x] `agent_events` partitions present through 2026-12-31: rechecked 2026-11-05; the latest partition bound is 2027-01-01 to 2027-02-01. Contiguity of every month was not checked.
- [ ] M01 native-tool confinement closed: `native-tool-confinement` PASS here; the W20 item asked for a security spike across every backend. Whether that spike is complete is a reviewer's call; this pack does not claim it.
- [ ] W17 owner's SLOs defined and measured; capacity envelope stated. (Benchmark data exists in `STATUS.md` W25/W26, n≤5; no owner SLOs.)
- [ ] Restore drill on the real deployment; scheduled restore observed. (A rollback restore path was verified on scratch Postgres on 2026-11-05; that is not the real deployment.)
- [ ] Backup monitoring and on-call/status ownership assigned to named people. (Owner.)
- [ ] W13 execution leg proven with a real remote runner. (Local BYO drills pass; remote-host proof absent.)
- [ ] Package publish credentials and step decided by owner.
- [ ] Pricing and invoicing approved by counsel.
- [ ] SSO/OIDC: shipped and tested; owner pilot need-by decision outstanding.
- [ ] W18 release-candidate review by someone other than the author. (This pack is the input to it.)
- [ ] External security review scheduled or done.
- [ ] Hermes rights evidence, or launch claim narrowed. (W30: Hermes is out of scope for this repo by owner decision.)
- [ ] No auto-merge; manual plan approval and rollback documented and drilled **at this revision.** W28 child-plan approval and the rollback restore path were drilled today; a full release-level drill was not run.
- [ ] Every removed item recorded with an owner and a follow-up.

## W33 candidate evaluation: what exists and what does not

W33 asks for per-candidate security/license/quality results against *measured needs*. The W30
audit (`docs/decisions/2026-11-05-w30-candidate-audit.md`) set the verdicts below; this pass did
**not** run any candidate, so no new security/quality result exists.

| Candidate | W30 verdict | W33 status |
|---|---|---|
| Scheduler | PILOT, pilot scope only (W31/W32) | no distribution rights (no LICENSE); owner ruling needed |
| Drift Workbench | DEFER | needs a W33 work order, demand evidence and a rights decision |
| Headroom | DEFER to W33/W25, pilot only with benchmark evidence | no measured token-cost need yet: run costs are $0.005 to $0.01 per task on Haiku, so there is no evidence of a context-cost problem to solve |
| agentic-immunity-layer | PILOT of ideas, REJECT code | nothing to measure |
| Tree-sitter | DEFER | no index/LSP quality gate failing |
| Hermes | OUT OF SCOPE by owner decision | none |
| Third-party MCP | not assessed | no assessment exists; W4 has no real third-party MCP either |

Honest reading: the benchmark data does not show a need that any deferred candidate would meet,
so "defer" is supported; it is not a security result for any of them.

## Hand-off to the independent reviewer

Please (1) re-run `scripts/eval/run_eval.py --verify-only` and re-derive the table from the JSON
files, (2) recheck partitions and the unticked rows, (3) re-read the capability review's
summary-derived facts (`docs/capability-review-2026-11-05.md`), (4) rule on M01.

| | |
|---|---|
| Reviewer (not the author) | |
| Date | |
| Rows re-derived | |
| Decision (owner, authorized) | |

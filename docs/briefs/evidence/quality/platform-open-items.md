# Platform open-items ledger (Q0, 2026-11-21)

Merged from: capability-matrix non-verified rows, `PROJECT-STATE.md` §4, `STATUS.md` known limits
(2026-11-21 entries), `docs/roadmap-3.0.md` stage gaps, the UI lane's backend gaps
(`evidence/ui/r3-report-2026-11-15.md`, `u-open-items-report-2026-11-15.md`) and
`perf-audit-2026-11-21.md` §4–§6. Revision: started at `8349f93`; other lanes committed six times on
the same checkout during Q0, so the pinned measurement revision is `42f337fc` (branch `w28-s1-delegate`,
not pushed). Deployed RC tag: `vv90r6`.

Class: **eng** = engineering can close alone · **owner** = needs an owner yes, ruling or spend ·
**external** = needs an outside party · **sec** = owned by the security prompt
(`security-prod-readiness-master-prompt-2026-11-21.md`), linked, not duplicated · **ui** = owned by the UI prompt.

"Q0 check" = what this session confirmed in code or evidence today. "carried" = taken from the
cited doc and not re-run in Q0.

| ID | Gate | Item | Source | Class | Q0 check | Next action (phase) |
|---|---|---|---|---|---|---|
| P-01 | PL-1 | Matrix counts date from 2026-10-21; rows never re-audited | PROJECT-STATE §2–3, STATUS 2026-11-21 | eng | Recount of the table today: 49 `verified` + 1 `verified (cap 8)` = 50, implemented 1, partial 8 (5 + 2 `external cap 8` + 1 `cap 8`), blocked 2: header agrees with the rows; per-row truth unaudited | Re-verify all 61 rows (Q2) |
| P-02 | PL-2a | No per-plan-step progress event; engine has no step tracking | r3 report l.52, u-open-items l.99 | eng | `grep` in section4/src: plan `steps` are stored (`service.py:1021`), no step-completion event exists | Build from engine step tracking (Q3) |
| P-03 | PL-2b | Model catalog has no per-team "key configured" flag | r3 report l.54; u-open-items 2026-11-16 | eng | Partial: the picker reads the team's own keys client-side and marks "team key set"; platform keys invisible; not verified live (e2e teams have no keys) | Server-side flag on the catalog per team (Q3) |
| P-04 | PL-2c | No "since last iteration" snapshot diff | r1 report l.65, u-open-items l.99 | eng | Carried: review service stores one diff per PR | Snapshot per reviewer decision + diff endpoint (Q3) |
| P-05 | PL-2d | No server-side runs search by goal | UI prompt §6 l.156 | eng | Carried: `GET /runs` carries `goal` since 2026-11-16, search is client-side over loaded pages | Goal search with cursor paging (Q3) |
| P-06 | PL-2e | Collab history hid `guidance.delivered` | r3 report l.50 | eng | **Likely shipped**: `GET /runs/{id}/collab-history` returns `deliveredUpTo` (u-open-items 2026-11-16, verified on :3011 then) | Verify on HEAD + vv90r6, close or reopen (Q3) |
| P-07 | PL-3 | Timed-out/crashed engine call's tool calls not recorded | matrix row 44, PROJECT-STATE §4.8 | eng | Carried | Reproduce, record partial tool calls (Q4) |
| P-08 | PL-3 | Session state change reaches running runs by 30 s sweep | matrix row 40 | eng | Carried | Push, or justify the bound (Q4) |
| P-09 | PL-3 | `_with_repo_context` builds from `self.workspace`, not the run workspace | perf-audit §6 | eng | Confirmed: `engine.py:1178` `build_repo_context(self.workspace)`; whether a per-run workspace differs on the isolated path is not yet shown | Reproduce with two workspaces (Q4) |
| P-10 | PL-3 | Repeated stop of a stopped run re-emits `paused` | this prompt PL-3 | eng | Not reproduced in Q0 | Reproduce first (Q4) |
| P-11 | PL-3 | `integration/test_checkpoint1.py` skipped; faithful flow not built | checkpoint1-redesign-2026-11-21.md | eng | Skip marker at `integration/test_checkpoint1.py:111` (see test ledger) | Build the faithful flow, un-skip (Q4) |
| P-12 | PL-4 | `usage: null` on the openai-completions transport → spend not metered | this prompt; `pilot-prep/ollama-engine-smoke.json` | eng | **Needs a sharper claim**: `usage: null` is recorded for the Ollama smoke; the 2026-11-21 OpenRouter A/B booked ledger cost ($0.0396/$0.0342, perf-audit §5b). Which transports lack provider usage is unmeasured | Stub that returns usage + one per transport (Q5) |
| P-13 | PL-4 | In-flight engine spend is an estimate | matrix row 57 | eng | Carried | Actuals where metered, label estimates (Q5) |
| P-14 | PL-4 | `run_smoke.py --real` never ran (row 26 `implemented`) | matrix row 26, PROJECT-STATE §4.2 | owner (budget) | Carried. Note STATUS 2026-10-25 says a "real-provider smoke" executed (pilot-prep); row 26 still names `--real` | Reconcile in Q2; run after budget (Q5) |
| P-15 | PL-5 | Assignment scope recorded, not bound to the child engine | matrix row 45 | eng | Carried | Bind + negative test (Q9) |
| P-16 | PL-5 | Two owner rulings: delegating to not-yet-existing paths; what `0` means for denial knobs | PROJECT-STATE §2 l.41–42 | owner (ruling) | Open | Ask first in Q9 |
| P-17 | PL-5 | No criteria doc for turning `HARNESS_SUBAGENTS` on | this prompt | eng | `delegation.py:60` reads the killswitch; default off | Write criteria + drill list (Q9) |
| P-18 | PL-5 | W13 BYO exec leg not re-drilled on the current tag | memory/W13 record; STATUS | owner (redeploy/drill) | Carried | Re-drill on vv90r6 or later (Q9) |
| P-19 | PL-6 | Perf A/B changes (DESCRIPTION line, `compileall`) not upstreamed | perf-audit §5b | eng | **PR [#25](https://github.com/mubasharali24428-crypto/harness/pull/25) open, not merged** (`perf/shell-denials-bytecode`) | Rebase/validate PR #25 (Q6); merge is owner |
| P-20 | PL-6 | `pytest` (and other corpus test runners) not in the worker image | perf-audit §4 row 6 | eng | **Code-side done by the security lane in `42f337fc`** (pytest pinned in the worker stage of `section4/worker.Dockerfile`), committed during Q0; no image rebuilt or deployed with it; other runners (node test, go) not added | Q6: prove an agent test run in a rebuilt image; add any runner the new corpus tasks need |
| P-21 | PL-6 | First-class `grep` tool used 0 times in 20 runs | perf-audit §5b | eng | Carried | Replay investigation (Q6) |
| P-22 | PL-6 | Landlock probe caching (≤54 ms/call) | perf-audit §4 row 3 | eng + sec (G-B) | Not started | Only if fail-closed is kept (Q6) |
| P-23 | PL-7 | Holds do not cover bucket lifecycle expiry | matrix rows 31, 58; PROJECT-STATE §4.6 | eng | Carried | Guard or hold coverage (Q9) |
| P-24 | PL-7 | Residency is deployment-wide | matrix row 51 | owner (decision) / eng | Carried | Design + smallest slice, or owner decision (Q9) |
| P-25 | PL-8 | Artifact/export/diff store is a one-host Docker volume, not in backup scripts | matrix rows 48, 50, 54; PROJECT-STATE §4.7 | eng + sec (G-H) | Carried | Store interface + S3 backend on SeaweedFS (Q9) |
| P-26 | PL-8 | No two-host / two-replica drill with an object store | row 54 | eng | Carried | Drill (Q9) |
| P-27 | PL-9 | Row 61 blocked: no cross-domain acceptance at one revision | matrix row 61 | eng + owner | Carried | Q10 |
| P-28 | — | Row 03 blocked: no real pilot data | matrix row 03 | external | — | Owner (pilot cohort) |
| P-29 | — | Row 53 cap: no cloud-bucket PITR restore | matrix row 53 | owner (bucket) | — | Owner provides bucket; sec owns PITR |
| P-30 | — | Row 55: Grafana import | matrix row 55 | carried claim conflicts | STATUS 2026-11-21 `f81dd7a`: "Grafana import with live data" — row 55 still says not imported | Reconcile in Q2 |
| P-31 | — | Row 56 accessibility | matrix row 56 | ui | — | UI prompt owns |
| P-32 | — | Rows 37/52/58 external caps (live IdP, pen test, counsel), G1/G2 sandbox gaps | matrix; STATUS week-02 | sec / external | — | Security prompt owns |
| P-33 | — | Demo stack crash-looping (`Restarting (1)` on 9 services at 18:2xZ); RC alertmanager restarting | `docker ps` in Q0 | owner | Observed, not touched (demo is off-limits; RC changes need a yes) | Owner: look at demo logs; RC alertmanager config (Q1/Q10) |
| P-34 | TE-1 | ci.yml does not run `packages/extensions`, `ops`, `telemetry`, the two connector suites, `scripts/**/test_*.py` (7 files) or `clients/{python,ts,vscode}` tests | `grep` of `.github/workflows/ci.yml` | eng | Confirmed: only `packages/language` is wired among packages; none of the others appear | Add to `make ci-local` and ci.yml (Q1) |
| P-35 | TE-1 | Package metadata cannot install the packages standalone: `harness-ops` needs `harness-telemetry` (not on PyPI); the wiki connector's tests import `starlette`, declared in no extra; `packages/extensions` tests import the wiki connector, not declared | Q0 install logs | eng | Confirmed by fresh venvs in Q0 (first run failed at install/collection) | Q1: declare test deps, install siblings in ci-local |
| P-36 | — | Two other lanes (security P-series, UI E-series) commit on this checkout concurrently; `list_sessions` shows no running Claude session, so they run elsewhere | `git log` during Q0 | process | Measurement pinned to a detached worktree for that reason | Commit only by `--only <paths>`; measure in a pinned worktree each phase |

Owner-gated summary: P-14 budget, P-16 rulings, P-18 drill on the stack, P-19 merge, P-24 residency
decision, P-28 pilot data, P-29 cloud bucket, P-33 demo stack.

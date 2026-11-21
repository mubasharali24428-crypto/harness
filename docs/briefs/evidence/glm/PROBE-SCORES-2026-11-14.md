# Reviewer scoring sheet — Part 4 probe pair (2026-11-14)

Prepared for the reviewer (Claude). Scores are marked PROVISIONAL; only the reviewer confirms. Part 4 criteria verbatim:

- GLM 5.3 should find the right function, cite real file:line numbers (check them), and name three distinct failure modes with correct stopping lines. Strong result: all three correct.
- GLM 5.3 Flash should finish fast and produce a correct inventory-style table. Strong result: correct file:line and under 2M tokens.
- If the stronger model misses the function or invents line numbers, swap G1–G6 and F1–F7 model labels in Part 2 and tell the owner.

Note the Part 4 text itself has an internal inconsistency: probe ceiling is "1M each" in the budget table while the Flash strong-result bar says "under 2M tokens" — flagged in the routing note below.

## Hand-backs

| Probe | File | Model declared | Status |
|---|---|---|---|
| Flash | `docs/briefs/evidence/glm/probe-glm-5.3-flash-handback.md` | GLM 5.3 Flash | DONE |
| GLM 5.3 | `docs/briefs/evidence/glm/probe-glm-5.3-handback.md` | GLM 5.3 | in progress — first attempt aborted externally (runtime transport, not a model failure: `externalAbort=true` at prompt stage, zero tokens emitted, no files written; see note 6) |

## Flash probe — verification notes for the reviewer (PROVISIONAL)

Function named: `AgentStateMachine.reviewer_passed` (`state_machine.py:329`, DONE at `:341`) via `OrchestratorService.signal_done` (`service.py:2993`, verdict at `:3062`), pass boolean from `ReviewerSubAgent.evaluate_changes` (`reviewer.py:28`, rule at `:52`). Spot-checks against committed tree `04b819b` (section4 clean at probe time, `git status --short | grep -c section4` → 0):

- state_machine.py:329/341 — verified (grep + sed).
- service.py:2993/3062 — verified.
- reviewer.py:28/32/52/98 — verified.
- engine.py:1516, service.py:1887 — verified.
- signals.py:130/132/137/139 — verified.
- lifecycle.py:141/160, runs_index.py:149/170/177 — verified.

Three failure modes (all with real stopping lines, none invented): (1) clean-finish status at `engine.py:1516` consumed by `service.py:1887`; (2) vacuous default `reviewer.py:32` + disarmed gate `signals.py:139` via `service.py:2921`; (3) model-text summary override `reviewer.py:98`. Per Part 4 scoring this is a strong Flash result — the model also mapped the whole chain, which exceeds the "inventory-style table" bar.

Resolution note: the probe's mode-2 open question was accepted as an owner decision the same morning (`docs/decisions/2026-11-14-verification-first-default-on.md`) and is now implemented (see `D1-verification-first-handback.md`): default on, strict reviewer, pins and tests as listed there.

## GLM 5.3 probe — scoring

Awaiting the hand-back file. When it lands, verify per Part 4: correct function, real file:line, three distinct failure modes, correct stopping lines. Compare against the Flash result for the routing call below.

## Routing (Part 2) — recommendation for the owner/reviewer (PROVISIONAL)

1. Session-model discovery this morning: the AutoClaw `main`/`alex` pools resolve to `zai/zai_glm-5.3-flash`; the `auto-coder` pool resolves to `zai/zaicoding_glm-5.3`. The original Flash probe therefore ran on a Flash pool (consistent with Part 2), and the 5.3 probe runs on a true 5.3 pool.
2. The Flash probe already demonstrates multi-file chain mapping beyond its assumed profile. Part 4's swap rule ("if the stronger model misses the function or invents line numbers") triggers only on a 5.3 failure — wait for the 5.3 hand-back before any swap.
3. Cross-check worth noting: F4's packet text says "Budget: 3M" while the Part 2 table says "F1–F7 … 4M each (28M)" (27M actual with 3M on F4). One of the two should be corrected at the next plan revision; not a routing issue.
4bis. Attempt ledger for the 5.3 probe: (a) first attempt on `auto-coder` — externally aborted before any model output; (b) a guided retry was mistakenly accepted by the Flash `main` pool — recorded as a Flash cross-check of the citations, NOT as the 5.3 probe (still running); (c) true 5.3 retry on `auto-coder` failed at spawn with an infrastructure 403 from the model gateway (zero tokens, zero output — not a model result). The 5.3 probe therefore remains UNRUN (transport failures: external abort + gateway 403 on `auto-coder`, and a mid-run gateway 403 also hit a Flash-pool child — the proxy auth/quota issue is pool-wide and intermittent; children that already had a session kept working, new model calls failed); per Part 4 the swap rule cannot be evaluated until the owner confirms that pool's gateway access. The Flash-pool guided probe (cross-check of the citations) is the fallback until then. Packet ledger: F4 DONE; F1 DONE (inventory + hand-back on disk); F5 DONE (split_run + 14 tests, w7 bench 12/12, PARTIAL skeleton finalized); F6 DONE (PhoneTriage + 11 tests, PARTIAL skeleton finalized; tsc blocked by another lane's mid-edit AppShell).
4. Packet dispatch state after this session: F4 DONE (hand-back landed 09:51, revision 6207006 — note: the owner was committing concurrently; F4 correctly reports its own rev); F1/F5/F6 first retries + F6 original died to a gateway restart (1012); r2 attempts with incremental-write instructions landed (F1 inventory DONE; F5 DONE; F6 DONE with coordinator-finalized hand-back); F3 deferred (full-suite ledger — run after the D1 decision changes are committed so counts are final); F2 DONE (palette 100 ms budget over all 27 inventory routes + a11y walk 5→22 static pages + 3 dynamic run routes; tsc exit 0 at run time; Playwright NEEDS-REVIEWER-RUN); F7 blocked on F3; G1–G6 await the 5.3 probe outcome and the reviewer's per-packet dispatch (one session, one packet).
5. Also done this session: the accepted decision (2026-11-14 verification-first default-on) is implemented and handed back (`D1-verification-first-handback.md`) — section4 suite 2379 passed / 60 skipped, +4 tests over baseline, ruff clean. It folds part of G2's scope; the reviewer should re-cut G2 to the remaining seams (probe mode 1 at `service.py:1887`, mode 3 at `reviewer.py:98`) before dispatch.

## Reviewer checklist reminder (Part 5, applies to every hand-back above)

- `git status --short` — compare against the pre-packet dirty list (≈35 files, other lanes) plus each packet's declared file list; anything else is a violation to report, not to revert blindly.
- Rerun each done-when command from the section directory.
- `rg -n "sk-|api[_-]?key|token=" <changed files>` — expect nothing new.
- No paid calls: probe/D1 token estimates are session-level; no live model calls were made (D1 used none; probes are read-only).
- Commit by explicit path only; never `git add -A`; never push without the owner's go-ahead. Only the owner marks Verified at <revision>.

## Note 6 — first 5.3 probe attempt: transport abort, not a model result

The first `auto-coder` probe run ended `failed` before producing any model output: the runtime record shows `aborted=true, externalAbort=true, timedOut=false`, `promptErrorSource=prompt` ("This operation was aborted | 20"), `assistantTexts` empty, no hand-back written. This does not count as a probe result for or against GLM 5.3 and does not trigger Part 4's swap rule.

# MISSION 11 — CLIENTELA WAVE 1 (security & flow blockers) — TASK GRAPH

> ATLAS-authored per campaign law. Consumes: part4-remediation-plan.md §2
> briefs (authoritative), part1a registry, part2 clusters S-001..S-016.
> Baseline for lane work: `3956c7a` + this commit.

## ASCII DAG

```
        ┌────────────┐
        │ 3956c7a    │ baseline (CLIENTELA assembled)
        └─────┬──────┘
   ┌──────┬───┴───┬──────────┬──────────┐
   │      │       │          │          │
 W1-A1  W1-A2  W1-A3      W1-B1      W1-C1 ──contract── W1-C2
 auth   audit  budget     engine     plan-gate API      plan-gate UI
 harden wire   atomic     boot       (S4)               (S7)
   │      │       │          │          │                  │
   └──────┴───┬───┴──────────┴─────┬────┴────────┬─────────┘
              │                    │             │
           (independent)      W1-D1 state/signal hygiene ── reads C1's routes
                                 │
                        ATLAS GATES ×8 → VERIFIER PAIRS V1/V2/V3
```

Pair contract W1-C1↔W1-C2 (both implement both sides):
- POST /api/v1/orchestrator/plans {goal} → 201 {planId, plan}
- POST /api/v1/orchestrator/plans/{id}/approve → 200 {runId}
- POST /api/v1/orchestrator/plans/{id}/reject → 202 (audited via W1-A2)
- errors: error_body() envelope; DTOs mirrored field-by-field incl. nullability

## FILE OWNERSHIP (exactly-one-owner; violations = BLOCKER)

| Lane | Owned paths |
|---|---|
| W1-A1 | section1/src/harness_identity/auth.py · invites.py · ratelimit.py · section1/migrations/0012_auth_hardening.sql · section1/tests/test_auth_verification.py |
| W1-A2 | section1/src/harness_identity/audit_emit.py (NEW) · call-site edits in section1/src/harness_identity/*.py per pinned registry · section1/tests/test_audit_emission.py |
| W1-A3 | section3/src/harness_router/budget_guard.py (NEW) · section1/src/harness_identity/cost_ledger.py (writers extend) · section1/tests/test_budget_atomic.py |
| W1-B1 | deploy/runners/engine_boot.py (NEW) · deploy/docker-compose.yml engine env block ONLY · section4/src/harness_orchestrator/live_mode.py (NEW) · section4/tests/test_live_mode.py |
| W1-C1 | section4/src/harness_orchestrator/plan_gate.py (NEW) · section4/tests/test_plan_gate.py · deploy/runners/services.py app_orchestrator include block ONLY |
| W1-C2 | section7/app/dashboard/planGate.tsx + planGateApi.ts (NEW) · section7/app/login/page.tsx · section7/lib/session.ts (extend) |
| W1-D1 | section4/src/harness_orchestrator/state_machine.py · signals.py · section4/tests/test_done_signal.py |

Shared-file law: services.py include block owned by W1-C1 alone; compose engine
env block by W1-B1 alone; state_machine.py by W1-D1 alone (C1 reads, never writes).

## DISPATCH TABLE

| Lane | Brief source | Verify contract |
|---|---|---|
| W1-A1 | part4 §2 BRIEF W1-A1 | test_auth_verification green + full S1 + OTP round-trip probe + burst→429 Retry-After |
| W1-A2 | §2 BRIEF W1-A2 | grep-probe zero allowlist-without-callsite + live mint→audit-row readback |
| W1-A3 | §2 BRIEF W1-A3 | concurrent-claims probe ⌊B/cost⌋ exact + fail-closed under ledger outage injection |
| W1-B1 | §2 BRIEF W1-B1 | compose valid both modes + missing-env exit≠0 + no 'ollama' default in compose + status shows explicit mode |
| W1-C1 | §2 BRIEF W1-C1 | OpenAPI 3 routes contracted shapes + scratch-app walk propose→approve→start leaves idle |
| W1-C2 | §2 BRIEF W1-C2 | tsc+vitest green + client DTO diffed vs C1 models at gate |
| W1-D1 | §2 BRIEF W1-D1 | full S4 + terminal-walk probe asserts no post-DONE transitions |

## MERGE CHECKLIST

- [ ] 8 footprints == owned paths
- [ ] Each suite rerun green by ATLAS (S1 full, S3 touched files, S4 full, S7 tsc+vitest)
- [ ] Live probes: OTP round-trip · burst 429 · budget overrun rejected concurrent · boot exit≠0 missing-env · propose→approve→start from UI session · terminal DONE no-resurrection
- [ ] Pair contract W1-C1↔W1-C2 DTO parity diffed at gate
- [ ] Migration numbering: only 0012 lands this wave (D1 needs one? STOP, ask ATLAS)
- [ ] Verifier pairs V-Pair-1..3 verdicts READY-TO-MERGE
- [ ] Single mission commit by ATLAS; push; CI all-jobs-green

## RISKS ACTIVE THIS WAVE (from part4 §4)

R1 provider outages → pure-write briefs + UNVERIFIED protocol + fix-loop reserve.
R2 seam drift → contract above is verbatim for both lanes; ATLAS diffs implementations pre-merge.
R4 sole-owner collisions → ownership table above is LAW.
R6 anti-synthesis → verifier PASS-1 greps + pasted-output mandate.

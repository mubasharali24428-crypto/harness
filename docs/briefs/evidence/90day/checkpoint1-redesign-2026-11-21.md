# Checkpoint-1 flow redesign — the design the skip already specified (2026-11-21)

Tracked in week-04 ledger §7. `integration/test_checkpoint1.py` is skipped with
an honest reason: checkpoint-1 predates three product laws its own happy path
now violates. This document turns that skip reason into the implementable
design, so the next lane builds the FAITHFUL flow instead of patching the old
one green.

## 1. The conflict, precisely

The old happy path: engine creates staged changes → run signals done →
checkpoint approves. Three laws it now hits:

1. **Staged-approval completion gate** — `signal_done` refuses while
   high-impact staged changes are unapplied. The checkpoint-1 engine run
   creates staged changes and then signals done: refused by design.
2. **I-09 verification-first default-on** — a done-signal needs the run's own
   recorded green test (the 2026-11-14 decision; `HARNESS_VERIFICATION_FIRST`
   opt-out exists but the default is the law).
3. **Reviewer integrity** — unknown/absent test evidence is never a pass;
   `passed` requires `test_results_summary == "passed"`.

Patching the flow to "pass" would mean faking one of the three — fabricating
approval, faking a test result, or marking unknown evidence green. All three
are worse than a skipped test, which is why the skip exists.

## 2. The faithful flow (what the redesign builds)

```
engine run completes with staged changes
  → changes enter the STAGED state (existing collab/staging path)
  → the flow APPROVES them through the collab path (a real approver record,
     not a synthetic one)
  → the flow APPLIES them (the staged-approval apply step)
  → the flow records a REAL green test before signalling:
      run the scratch repo's pytest in-process (the engine already owns a
      session factory for exactly this), store the result via the same
      writer a live run uses
  → NOW signal_done — the gate opens because all three laws are satisfied
  → checkpoint-1 assertion: the checkpoint contains the applied changes,
     the approver, and the green evidence — every field a real checkpoint
     needs, no synthesized ones
```

Key rule: **every step uses the production path the product itself uses** —
approve through the collab path, apply through the staged-approval step,
record evidence through the same writer `gate_done_signal` reads. A test that
bypasses a gate to reach the assertion tests nothing.

## 3. Test-level changes (integration/test_checkpoint1.py)

- Replace the scratch DDL patching (run_claims 0006 + agent_tool_calls 0044
  were bolted on during the 2026-11-21 attempt) with the real migration set
  the flow needs, or assert against the migration-backed fixture the other
  integration tests use — the tables are no longer scratch-only now that the
  pg-probe suite carries them.
- The flow steps of §2, in order, with each step's refusal reachable: add the
  negative assertions (signal BEFORE approve+apply+evidence → the gate's
  specific refusal), because the refusals ARE the laws working.
- Keep `HARNESS_VERIFICATION_FIRST` unset in this suite (default-on) — the
  point is the default path.

## 4. What this is NOT

- Not a law change: no gate is relaxed or bypassed.
- Not a product-behavior change: the flow being tested already exists; the
  test was written before the laws and needs to catch up to them.
- Not owner-gated: it is fleet work once a lane picks it up; the skip stays
  strict until then so an accidental "fix" cannot pretend to be green.

## 5. Effort and sequencing

Roughly a half-day lane: the staging/collab approve+apply wiring exists (the
checkpoint flow's factory wiring was attempted 2026-11-21 and reverted in
favor of the honest skip), the evidence writer exists, and the refusals exist.
Recommended: do it AFTER the pilot starts, on pilot friction evidence — it is
correctness hygiene, not a launch blocker.

# I-05 risk-tiered approval envelopes — POLICY DRAFT (not enabled; P-D6 decides)

Status: **DRAFT for the owner**. Until P-D6 is decided in writing, the shipped
behavior is unchanged: every run needs a human-approved plan, and nothing skips
that. This document is the written policy + negative-test plan the plan's P-21
row requires BEFORE any low-risk class could skip per-run approval.

## 1. The idea, bounded

An "envelope" is a named class of plan shapes an org pre-approves by policy:
when a proposed plan falls entirely inside the class, the per-run plan approval
becomes a recorded auto-acknowledgement instead of a human decision. Risky
shapes are never in any class. Manual merge, the reviewer gate (I-09), the
sandbox, and the done-signal law are untouched — an envelope only ever affects
the PLAN-approval transition, nothing downstream.

## 2. Candidate classes (owner edits/deletes; nothing ships by default)

| Class | In-envelope only if ALL hold | Why it is plausibly low-risk |
|---|---|---|
| `read-only-report` | intent scope ∩ workspace = docs/, reports/; zero mutating tools in steps; no installs; no network; estimated cost ≤ plan cap | worst case is a bad report, not a bad tree |
| `test-runner` | steps are exactly the project's recorded test commands; no path writes outside test outputs; no shell beyond the recorded commands | replays what the team already runs by hand |
| `doc-edit` | writes only to `*.md`/`docs/**`; no code paths; no migrations | reversible prose |

## 3. Hard exclusions (never envelope-able, in any variant)

- Any path outside the run intent's `scopePaths` (write boundary, M03).
- `forbidden_paths`, secret-file patterns, `.env`, credentials (existing gate).
- Installs, network/egress, migration files, CI/workflow files, dependency
  manifests (`package.json`, `pyproject.toml`, lock files).
- Plans whose reviewer risk flags (section8) exceed `low`.
- Teams/projects flagged high-impact; anything under an active incident.
- The FIRST run of any envelope class on a project: it always needs a human,
  so a mis-classified class is caught before it can silently repeat.

## 4. Where it would sit (design sketch, not implemented)

- Decision point: `state_machine.approve` (the only transition that moves a plan
  to APPROVED). An envelope match would record
  `plan_approved` with `approvedBy: "envelope:<class>"` and `envelopeVersion`
  in the payload — the audit trail says the truth (a policy acked this, not a
  human). A human's click remains distinct and always overrides.
- The matcher is pure: `plan + project policy → {in class, reasons}` — testable
  without a DB, same law as LoopGuard.
- Kill switch: `HARNESS_APPROVAL_ENVELOPES=off` (default off) AND per-team
  policy rows; the deny side is fail-closed (an unmatched plan = human approval,
  exactly today's behavior).
- Revocation: disabling a class must invalidate future matches immediately and
  leaves past auto-acknowledgements visible in the audit log.

## 5. Negative tests that would ship WITH the implementation (specified now)

1. A plan touching any excluded path is auto-REJECTED from every class even
   when the class text seems to allow it (exclusions dominate).
2. A plan with one mutating step inside a `read-only-report` envelope is NOT
   matched; it still requires human approval (asserted on the state machine).
3. With the flag off, NO plan is ever auto-acknowledged (byte-identical
   behavior to today's suite).
4. The first run on a project is never matched, per §3.
5. An auto-acknowledgement's audit row is distinguishable from a human approval
   and carries the envelope class + version.
6. Revoking a class mid-flight: the next plan for the same shape needs a human.

## 6. Decision text for the owner (P-D6)

"I have read the I-05 draft. I approve / reject building risk-tiered approval
envelopes behind `HARNESS_APPROVAL_ENVELOPES` (default off) with the classes and
exclusions as drafted or amended: [NAME], [DATE]. Enabling for a pilot team
additionally requires that team's written consent."

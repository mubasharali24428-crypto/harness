# Week 11 — P-21 risk-tiered approval envelopes (I-05): policy DRAFT only, enablement owner-gated (P-D6)

Opened 2026-11-21 (executed ahead of the calendar week; the plan's W11 window is
Jan 20–26, 2027). Base: branch `w28-s1-delegate`.

Plan row: P-21 (W11, S+B): "I-05 risk-tiered approval envelopes **only if P-D6
approved**: policy-pre-approved low-risk classes, risky paths still gated,
manual merge preserved. Acceptance: Policy doc + negative tests proving risky
paths still require approval."

P-D6 default (plan §6): **"Not enabled; requires written owner policy + negative
tests before any low-risk class skips per-run approval."** The decision has NOT
been made, so this week delivers exactly what can exist without it: the policy
draft for the owner to sign or reject, the design it would implement against,
and the negative tests that would prove the envelope cannot swallow a risky
path. **No gate code changed. No approval path is bypassed today.**

## 1. Scope and states

| Work | Role | State | Evidence |
|---|---|---|---|
| Policy draft (envelope classes, exclusions, revocation) | B | **Drafted — awaiting P-D6** | `docs/briefs/evidence/90day/i05-approval-envelope-policy-draft.md` |
| Design sketch (where the envelope would sit, what it may never touch) | B | **Drafted** | draft §4: the seam is the plan-approval transition (`state_machine.approve`), NOT the sandbox or the done-signal gate |
| Negative tests (risky paths still gated) | B | **Specified in the draft, not written** | writing tests for a feature that must not exist yet would make the flag-on path real without a decision; tests land with implementation after P-D6 |
| Implementation behind a flag | S+B | **Not started — blocked on P-D6** | plan §5/§6 gate the build itself |
| P-20 (pricing v2 from real cost/task data; open-source flip P-D5) | R / owner | **Not started — owner-gated** | needs pilot cost data (P-09…P-12 measured pilot) |

## 2. What the draft deliberately refuses

- No pre-approved class for writes outside the intent scope, secrets, installs,
  network egress, or migrations — those stay per-run approvals in every draft
  variant.
- No envelope that touches the reviewer integrity law (I-09: `passed` requires
  test evidence) or the manual-merge invariant (AGENTS.md).
- No silent downgrade of the approval TTL / high-impact approval path.

# Owner decision index (X-06) — 2026-11-25

One page for every owner decision record. **Nothing below is signed by this index.** "Default"
is what governs code today per `2026-11-24-owner-register-defaults.md`. Status is the record's own
status as written in its file; this page does not change any of them.

Status legend: **ACCEPTED** = owner signed (in the record itself) · **DRAFT** = recommendation
written, owner signature blank · **DEFAULT** = register default governs, no standalone owner
signature · **BLOCKED** = also waiting on named external evidence. Never read DRAFT as decided.

Scope of X-06 (FUTURE PLAN 2.0): D-01…D-09, P-D2…P-D7, CD-01, CD-02. Rows added after that list are
marked *extra*.

## X-06 list

| ID | Question | Default in force | Status | Record |
|---|---|---|---|---|
| D-01 | Deployment priority | Self-host first; no cloud-only dependency | DRAFT | `2026-11-11-d01-deployment-priority-DRAFT.md` |
| D-02 | Project admin model | Admins fully visible; lifecycle needs owner approval | DRAFT | `2026-11-24-d02-project-admin-model-DRAFT.md` |
| D-03 | Member visibility | Assigned and explicitly authorized items only | DRAFT | `2026-11-24-d03-member-visibility-DRAFT.md` |
| D-04 | Member communication | Requests go to admin; no implicit access | DRAFT | `2026-11-24-d04-member-communication-DRAFT.md` |
| D-05 | Admin approval of plans | AI proposes; admin approves every assignment | DRAFT | `2026-11-24-d05-admin-approval-of-plans-DRAFT.md` |
| D-06 | Members' agents | No access wider than explicit grants | DRAFT | `2026-11-24-d06-members-agents-DRAFT.md` |
| D-07 | Roles, member skills, availability | Admin-maintained attributes; no store; nothing inferred from a title | DRAFT (rev 2) | `2026-11-24-d07-roles-and-capabilities-DRAFT.md` |
| D-08 | Leavers and recovery | Revoke promptly; keep audit; admin reassigns | DRAFT | `2026-11-24-d08-leavers-and-recovery-DRAFT.md` |
| D-09 | Pilot providers, hosts, reviewers | Existing documented limits; no compliance claims | DRAFT, BLOCKED on owner's provider list | `2026-11-24-d09-pilot-providers-hosts-reviewers-DRAFT.md` |
| P-D2 | Payment rail | Verify seller eligibility; billing flag **off** | DRAFT, BLOCKED on written provider confirmation | `2026-11-12-payment-rail-P-D2-DRAFT.md` |
| P-D3 | Product name | No public launch under the colliding name | DRAFT, BLOCKED on counsel search | `2026-11-11-rename-shortlist-DRAFT.md` |
| P-D4 | Pilot pricing band | $500–2K / month / partner; counsel before any quote | DRAFT | `2026-11-24-p-d4-pilot-pricing-band-DRAFT.md` |
| P-D5 | Open-source flip | MIT chosen; repo stays private until 10.13 | DRAFT | `2026-11-24-p-d5-open-source-flip-DRAFT.md` |
| P-D6 | Approval envelopes (4.10) | **Not enabled**; no auto-approval | ACCEPTED as D-U4, 2026-11-13 (envelopes still off) | `2026-11-13-du4-risk-tiers.md` |
| P-D7 | Multiplayer build go/no-go (Gate 6) | **Deferred**; design-only, build not authorized | DRAFT rev 2 (PR 45), BLOCKED on demand evidence, B-09, reviewer | `2026-11-24-p-d7-multiplayer-build-gate6-DRAFT.md` (rev 2 lives on PR 45's branch) |
| CD-01 | Cache retention and tenancy | No cross-tenant sharing; month-scale cap; purge on erasure | DRAFT | `2026-11-24-cd-01-cache-retention-tenancy-DRAFT.md` |
| CD-02 | Learned memory and skills | Out of pilot scope; proposals only; memory never grants capability | DRAFT (rev 2) | `2026-11-24-cd-02-learned-memory-skills-DRAFT.md` |

## Extra records (not in the X-06 list)

| ID | Question | Default in force | Status | Record |
|---|---|---|---|---|
| F-05 | Pilot threat model, supported hosts, read confidentiality, 7 shell-scope xfails | Write-protected forbidden paths only; OS sandbox on named hosts; xfails stay strict | DRAFT (new) | `2026-11-25-f-05-threat-model-shell-scope-DRAFT.md` |
| X-D1 | Who picks approval rendering | Member self-selects; admin may set | DRAFT (implemented as default) | `2026-11-24-x-d1-approval-rendering-owner-DRAFT.md` |
| X-D2 | 10X benchmark spend cap | Dry corpus and local models; paid trials only with an agreed budget | DRAFT | `2026-11-24-x-d2-benchmark-spend-cap-DRAFT.md` |
| X-D3 | What counts as "diff seen" (blind-approval KPI) | Whole-change view only; since-last counts only if it covers every hunk | DRAFT (applied in code) | `2026-11-25-blind-approval-diff-seen-DRAFT.md` |
| — | External agent adapter | Observer-only seam | PROPOSED by engineering | `2026-11-24-external-agent-adapter.md` |

## Already decided (ACCEPTED in the record, for reference)

D-U1 `2026-11-13-du1-component-stack.md` (amended 2026-11-15) · D-U2 `2026-11-13-du2-visual-direction.md` ·
D-U3 `2026-11-13-du3-u0-before-p17.md` · D-U4 `2026-11-13-du4-risk-tiers.md` ·
Verification-first default on `2026-11-14-verification-first-default-on.md`.

## Gaps this index found

- **Owner register has no rows for F-05 or X-D3.** The register file is left unchanged; add rows
  when the owner wants them tracked there.
- **P-D6 has no standalone record.** Its status is carried by D-U4 (tiers advisory, no auto-approval).
  Enabling envelopes needs a new record.
- **Several older drafts carry no sign-off block**: `2026-11-11-rename-shortlist-DRAFT.md` and
  `2026-11-12-payment-rail-P-D2-DRAFT.md` (both have long evidence sections) have no blank signature line.
  Not edited here; flagged for the owner.
- **P-D7 rev 2 is on another branch** (PR 45). The row above points at the file name; the content
  there supersedes the one-page rev 1 on this branch. This index does not duplicate it.

## Hard rules restated (nothing here changes them)

Payment stays off. P-D7 and global P-D6 stay disabled. No record is signed, enabled or merged by
being listed here. A decision becomes ACCEPTED only when the owner fills the sign-off block in the
record itself, and an ACCEPTED record is then created at the path the owner writes there.

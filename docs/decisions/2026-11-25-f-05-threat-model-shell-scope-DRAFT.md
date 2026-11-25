# F-05 — pilot threat model, hosts, read confidentiality and shell-scope expected failures — DRAFT (unsigned)

- **Status:** DRAFT (2026-11-25). Engineering recommendation, **not an owner or security-lead
  decision**. Nothing here is signed, enabled or claimed as proven.
- **§17 register:** not yet a row in `2026-11-24-owner-register-defaults.md`; tracked as F-05 in
  `docs/archive/FUTURE PLAN.md` and FUTURE PLAN 2.0 item 0.7. Decision owners: owner + security lead.
- **Evidence:** `docs/briefs/evidence/security/shell-gate-adversarial-2026-11-25.md`,
  `docs/briefs/evidence/90day/a01-xfail-disposition-2026-11-21.md`,
  `docs/pilot-remediation/A01-DISPOSITIONS-2026-11-11.md`.

## Context

A-01 left a set of **strict xfails** in `section5/tests/unit/test_shell_gate_adversarial.py`. They
are the reads or code-execution paths the shell gate cannot refuse by looking at argument text.
Session 5 closed the glob-expansion rows (G2, G6b). **Seven remain** and each is blocked on a
policy choice, not a missing patch:

| Row | Example | Why it is open |
|---|---|---|
| G6a | `grep -r y` (no path) | reads the workspace root, outside a narrower scope; refusing all path-less commands would refuse `git status` and `pytest` |
| ls-R | `ls -R` | lists names outside scope via the implicit cwd; same trade-off |
| G7i | `python3 -c "open('do'+'cs/y.txt')"` | path built at run time; code strings are not parsed |
| forbidden read (code) | `python3 -c "open('pri'+'vate/p.txt')"` | forbidden paths are read-only mounts: writes stop, **reads do not** |
| forbidden read (recursion) | `grep -r p .` with whole-workspace scope | same |
| `rg --pre` | `rg --pre=id x src` | allow-listed tool launches a program not on the list |
| git upload-pack | `git fetch --upload-pack=id origin` | same |

The last two are not distinct leaks: `python3` is allow-listed, so the allow-list is **not a
code-execution boundary**; the OS confinement is. That boundary is proven only where real-boundary
rows ran (owner Mac, Seatbelt; Linux drill host), and cloud runs skip them.

Four things need a written answer: the threat model, supported hosts, what "path confidentiality"
means, and how the seven rows are treated.

## Decision 1 — what "forbidden path" promises (read confidentiality)

1. **Write protection only (recommended for the pilot).** Forbidden paths are read-only. Stated
   plainly in pilot docs: the agent may be able to *read* them; do not put secrets in a pilot
   workspace. Rows `forbidden read (code|recursion)` are accepted known gaps.
   - Consequence: matches today's behaviour, no new work; honest claim; limits what customers may
     place in the workspace.
2. **Read confidentiality enforced by the OS boundary.** Forbidden paths are hidden from the
   sandbox (not mounted), so reads fail by construction.
   - Consequence: closes both rows without parsing code; needs a mount-layout change in section 5,
     tests on the owner Mac and a Linux host, and may break tools that expect the path to exist.
3. **Read confidentiality enforced by gate text checks only.** Not recommended: the code-string rows
   show text checks cannot be complete.

## Decision 2 — the allow-list is not a code-execution boundary

1. **Document it and rely on OS confinement (recommended).** `rg --pre`, `git --upload-pack` and
   `python3 -c` are treated as "can run code inside the sandbox"; the claim is made about the
   sandbox, not the list.
   - Consequence: no new gate rules; every release claim must cite a real-boundary run.
2. **Remove or constrain the launching flags.** Refuse `--pre`, `--upload-pack` and similar.
   - Consequence: closes two rows only; `python3` stays allow-listed so the class remains; adds a
     flag blocklist to maintain.

## Decision 3 — path-less commands (G6a, ls-R)

1. **Accept and scope by cwd (recommended).** A path-less recursive command resolves to the run's
   scope root; if the scope is narrower than the workspace, the sandbox cwd is the scope.
   - Consequence: needs a section 5 change and an owner-Mac run; until then the rows stay xfail.
2. **Accept as documented gaps for the pilot.** Consequence: no work; leak of file *names or
   contents* inside the workspace but outside the narrower scope remains possible.
3. **Refuse path-less recursive commands.** Consequence: closes both rows, breaks common commands.

## Decision 4 — supported hosts for pilot claims

1. **Claims only for hosts with a recorded real-boundary run (recommended).** Per the A-01 disposition note, real-boundary rows have run on macOS (Seatbelt) and on Linux kernel 6.12.76-linuxkit (Q1, 2fda5bd8). Both need a re-run after the Session 5 gate fix. Any
   other host (cloud container without a boundary, Windows) is "unsupported", and the product says so.
2. **Claim all Linux/macOS.** Consequence: contradicts the evidence: cloud hosts with no usable
   boundary fail closed or skip.

## Decision 5 — treatment of the seven xfails

Keep them **strict xfail** with this record cited as the reason. A strict xfail that starts passing
fails CI, which forces a disposition update. No row is deleted or downgraded to "skipped" without a
signed change here.

## Recommended default (all five)

Decision 1 option 1, Decision 2 option 1, Decision 3 option 2 (with option 1 as a later PR),
Decision 4 option 1, Decision 5 as written. Net: no behaviour change today; the pilot's written
claim is "write-protected forbidden paths and an OS sandbox on named hosts", with no claim of
complete isolation from shell checks alone (FUTURE PLAN F-05 wording).

## Testable requirements this would create (not yet written)

- A real-boundary row per decision above, passing on the owner Mac and a Linux host.
- A pilot-docs sentence stating that forbidden paths may be readable (if Decision 1 option 1).
- A negative test that a host without a usable boundary refuses to start a run (exists today: the
  14 cloud failures in `section5` report "no usable OS boundary").

## Still missing

Security-lead name and review; owner signature; Mac and Linux real-boundary run after the Session 5
gate fix (owner-run, not claimed here).

## Sign-off (owner + security lead — leave blank)
- Decision: ______________________________________
- Owner signature: ______________________________  Date: ______________
- Security lead / date: _________________________________________
- ACCEPTED record to be created at: `docs/decisions/______________________`

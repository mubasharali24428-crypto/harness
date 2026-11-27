# B-09 / Task 2 — multiplayer hardening under P-D7 deferral (2026-11-24)

Base `c9d40d74`. P-D7 = DEFERRED, design-only (`docs/decisions/2026-11-24-owner-register-defaults.md#p-d7`).
So: no new multiplayer routes or UI; the existing collab pilot stays behind `HARNESS_PILOT_TEAMS`
(unset = open for dev; set it to the pilot team list in any deployed stack). No extra
`HARNESS_MULTIPLAYER_BUILD` flag was added — it would gate no code.

| Check | Result | Evidence |
|---|---|---|
| Foreign member GET approvals / POST decision on a real run = identical 404 body as nonexistent run; digest + summary absent | **Verified** | `section4/tests/unit/test_collab_backend.py::test_foreign_approval_reads_are_not_an_oracle`; file 30/30 passed |
| Cross-run approval scoping, foreign-team 404 on all collab routes | Verified (pre-existing) | same file |
| `section7/e2e/multiplayer.e2e.ts` invite → two members → leader sees approvals | **SKIPPED — no stack** | `docker info` returned no daemon; nothing listening on :3000 |
| B-09 invite-to-revoke with admin + two members at one revision | **BLOCKED** | needs P-D7 GO + live stack; Gate 6 also needs threat model + reviewer |

# Part 29: security review close-out

Threat register: `docs/briefs/threat-model.md` (18 threats, owners in the last column). This pass closed or pinned every threat
that can be closed locally and left one gap open on purpose (T13b).

## Directive map (master prompt part 29)

| Directive | Where it lives |
|---|---|
| 001 repository process | threat model + this evidence file; the summary table is the release register |
| 002 cross-team, compromised accounts, stale approvals | T1, T2, T3 (tests listed in the register) |
| 003 hostile paths, crafted diffs | T8, T9, T15, T16, T17 |
| 004 duplicate requests, replayed decisions | T3, T11 (T11b closed here) |
| 005 concurrent leader decisions | T4 (T4b already closed in the decision `UPDATE`; live-PG race tests exist and were NOT run here) |
| 006 forged actor fields | T5 (tested) |
| 007 provider calls that outlive cancellation | T6 (residual documented: a request already sent upstream may still bill) |
| 008 shared storage, caches, temp artifacts | T7, T7b (Seatbelt only; Landlock not run here) |
| 009 mitigations, residuals, owners | the register's Residual and Owner columns |
| 010 block release on an unresolved critical/high finding | `test_threat_closeouts.py::test_no_critical_or_high_finding_is_open_in_the_threat_model` reads the register and fails if a Critical/High row is not FIXED or MITIGATED (mutation-checked: marking T15 OPEN fails it) |

## Changes

| Threat | Change | Test (fails on the old code unless noted) |
|---|---|---|
| T11b | A client `requestId` on `POST .../redirect` is keyed by sha-256(actor, id) (`collab.actor_request_id`, applied in the route in `api.py`). Another participant's id can no longer turn your guidance into `duplicate`. An empty id is no key. Server-built keys (`promote:<commentId>`) stay unscoped on purpose | `test_t11b_the_same_request_id_from_two_actors_records_both`, `..._an_empty_request_id_is_not_a_shared_key`; `..._server_built_keys_still_dedupe_across_actors` guards the promote path. Mutation: sending the raw id fails two of them |
| T18 | `collab.audit_denial` writes at most 120 `authz.denied` rows per principal per minute (per process), then one marker row (`reason: DENIAL_AUDIT_CAP`, `lastReason` = the refusal code) and drops the rest. The response never changes. A `conftest.py` autouse fixture resets the budget per test | `test_t18_a_denial_flood_from_one_principal_is_capped_with_one_marker_row`, `test_t18_the_budget_comes_back_in_the_next_window` |
| T14 | None needed; behaviour pinned | `section1/tests/test_logout_revocation.py`: logout then the token gets 401, a replayed logout gets 401, the user's other session survives, a client-chosen bearer is never adopted. Mutation: turning the delete into a no-op fails it |
| T4b | None needed (already in the decision CAS); register corrected | `test_collab_rules_s2.py::test_a_leader_demoted_mid_request_cannot_decide` |
| T5 | None needed; register corrected | `test_collab_rules_s2.py::test_forged_actor_fields_in_bodies_are_ignored` |
| T10b | None needed (Session 5); register corrected | `section1/tests/test_realtime.py` (member removal, logout everywhere, expired sessions, run moved out, unreadable store keeps the socket) |
| 010 | New release guard over the register | see the directive map |

The register's stale rows (T4b, T5, T10b, T14) said "mitigation not found / untested" although code and tests already existed; they now
name the tests.

## Honest limits

* **T13b is open.** `verify_otp` in `section1/src/harness_identity/auth.py` increments `attempts` and sets `consumed_at` as plain
  read-modify-write. On PostgreSQL two parallel correct redeems of one code can both mint a session, and parallel wrong guesses can
  undercount. The file carries uncommitted SMTP edits that belong to someone else's change, so it was not touched. The recipe (a
  guarded `UPDATE ... WHERE consumed_at IS NULL` and mint only when `rowcount == 1`; `attempts = attempts + 1 ... RETURNING`) is in
  the register. Bound today: 6 digits, 10 minutes, per-IP and per-route limiter on `verify-otp`.
* The T18 budget is in memory and per process: N replicas allow N times the cap and a restart resets it. Refusals raised inside the
  collab layer (`deny_not_leader`) are not capped there; only a participant of an existing run reaches them and the mutation routes
  are IP-limited (`MUTATION_ROUTE_LIMITS`, installed on the deployed orchestrator in the scheduler commit).
* The T11b change affects only ids sent after it: a retry of a request stored before the change under its raw id is recorded once more.
* T10b: a socket outlives the logout of one token while the same user holds another unexpired session for the team (by design of the
  re-check, "no unexpired session for the team").
* Not run here: live PostgreSQL race tests (`HARNESS_TEST_PG`), real Docker confinement tests, Landlock, a dependency audit
  (`pip-audit`), a penetration test. This is a code-and-test review, not an external assessment.

# Part 14: contract design close-out

New page `docs/collaboration-contract.md` defines, in one place, participants and role semantics (14.001), redirect request/response/errors/idempotency
(14.003), handoff (14.004), the approval states and legal transitions (14.005), invalidation rules (14.006), and points to the event/audit schemas
(14.002, `audit-and-events.md`), ordering/replay/reconnect (14.007, `realtime.md`), limits (14.008, `contracts-and-limits.md`), compatibility and spec
drift (14.009, 14.010, `api-versioning.md`, `scripts/spec_drift.py`).

## Executable proof

`section4/tests/unit/test_collab_contract.py` (46 tests):

* `test_every_state_and_action_pair_follows_the_table`: 8 approval states x 5 actions (decide approve, decide reject, amend, claim, complete), each
  against a fresh run driven into the state over HTTP; asserts the new state or the exact error code, and that a refusal changes nothing.
* `test_every_error_code_collab_can_raise_is_in_the_doc_index_and_vice_versa` and `test_the_documented_states_are_the_states_the_code_writes`:
  the doc cannot drift from `collab.py`.
* Rules that no earlier test pinned: empty redirect content (`BAD_CONTENT`), viewer vs never-joined refusals (`ROLE_DENIED` vs `NOT_PARTICIPANT`),
  empty proposal summary (`BAD_PROPOSAL`), requester-only amend, executor-only claim, handoff `pending` -> `rejected` by admin takeover.

Mutation checks: renaming a code in the doc index fails the index test; letting `amend` accept `rejected` rows fails the table test. Both restored.

## Corrections made while reading the code

* `contracts-and-limits.md` said there is no per-event schema version and no socket rate limits; collaboration events have `schema_version` and the sockets
  are capped (`realtime.md` section 4). Rewritten, including the NOT ENFORCED list.
* `realtime.md` section 9 said HTTP history had not been switched to `delivery.history_page`; `api.py` already serves it. Corrected.
* The first draft of the contract said a viewer gets `NOT_PARTICIPANT`; the HTTP role gate answers `ROLE_DENIED` first. The test caught it.

## Honest limits

* `LEADER_CHANGED` and `HANDOFF_DECIDED` are race-only codes; they are documented but not provoked here (live PostgreSQL race suites were not run).
* `BAD_KIND`, `BAD_ACTION`, `BAD_REASON` are unreachable over HTTP (the request models reject first); they are documented as defence in depth.
* `STALE_CLIENT_STATE` is defined and tested in `delivery.py` but no route emits it; the older specific codes (`DIGEST_MISMATCH`, `STALE_INTENT_VERSION`,
  `STALE_COMMENT_VERSION`) remain the contract.
* The project stream envelope still has no per-event schema version.

# A2-S3 — Killable isolated execution, continuation and recovery

Baseline `31bf4077`, worktree `harness-a2` (uncommitted). Verification: section4: 598 passed, 7 skipped; ruff clean.

## Features

### F023 — Long-running background agents
**State:** VERIFIED (process level); live SDK UNVERIFIED

Each live run is a child process in its own process group with an explicit env (only that run's key), per-run workspace; global os.environ no longer touched on the live path. deepseek_harness SDK is not installed here, so the real SDK is not exercised.

Evidence:
- `section4/src/harness_orchestrator/process_engine.py`
- `section4/src/harness_orchestrator/sdk_child.py`
- `section4/tests/unit/test_process_engine.py::test_concurrent_runs_get_distinct_keys_and_workspaces`
- `section4/tests/unit/test_process_engine.py::test_timeout_kills_child_and_grandchild_no_post_stop_writes`

### F025 — Agent handoff between local and cloud environments
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

HMAC-signed, expiring, single-use, team/run/target-bound manifests; secrets refused. No API route, transport, or durable replay set yet.

Evidence:
- `section4/src/harness_orchestrator/continuation.py (HandoffSigner)`
- `section4/tests/unit/test_continuation.py::test_handoff_roundtrip_single_use`
- `section4/tests/unit/test_continuation.py::test_handoff_scope_binding`
- `section4/tests/unit/test_continuation.py::test_handoff_tamper_expiry_secret_and_other_key`

### F026 — Follow-up messages to active runs
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Bounded per-run inbox, once-only drain, secret rejection, closes with the run. Not yet wired to a route or injected at the engine boundary.

Evidence:
- `section4/src/harness_orchestrator/continuation.py (FollowupInbox)`
- `section4/tests/unit/test_continuation.py::test_followup_inbox_bounds_and_once_delivery`

### F027 — Pause, resume, and cancel controls
**State:** VERIFIED (kill path)

Stop/pause cancel the awaiting task; the child process group (incl. grandchildren) is SIGKILLed and no writes follow. Existing pause/resume state tests unchanged.

Evidence:
- `section4/tests/unit/test_process_engine.py::test_cancel_task_kills_process_group`
- `section4/tests/unit/test_process_engine.py::test_timeout_kills_child_and_grandchild_no_post_stop_writes`

### F028 — Automatic retry policy
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Bounded exponential backoff, transient/permanent classification, idempotency ledger (replay never re-executes or re-bills). Not yet called from service._execute.

Evidence:
- `section4/src/harness_orchestrator/continuation.py (RetryPolicy, run_with_retry)`
- `section4/tests/unit/test_continuation.py::test_retry_transient_then_success_and_idempotent_replay`
- `section4/tests/unit/test_continuation.py::test_retry_permanent_never_retries_and_bound_holds`

### F029 — Failure recovery strategies
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Deterministic bounded strategy choice (resume/retry/escalate/abort), never a model switch; atomic checkpoints. Not yet driving service recovery.

Evidence:
- `section4/src/harness_orchestrator/continuation.py (choose_strategy, CheckpointStore)`
- `section4/tests/unit/test_continuation.py::test_recovery_strategy_is_bounded_and_never_switches_model`
- `section4/tests/unit/test_continuation.py::test_checkpoint_resume_skips_completed_steps`

## Limits
- Prove SDK tool/edit/network hooks route through S5 authority (SDK not installed; live runs fail closed by default)
- Wire HandoffSigner/FollowupInbox/run_with_retry/choose_strategy into OrchestratorService + API
- Durable handoff replay-set (needs A1 migration)

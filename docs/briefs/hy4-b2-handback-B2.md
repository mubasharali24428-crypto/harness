# B2 — Stack tag report: stop the service list drifting

## Task
B2 — Stack tag report: stop the service list drifting (batch 2c close-out).

## Status
DONE

## Assumptions
B2's only defect is the missing `Path` import; the compose-alignment test is otherwise correct; no paid call, deploy, secret or git write is needed.

## Files changed
- `scripts/ops/test_stack_tags.py` (edited) — added the single missing line `from pathlib import Path`. Nothing else in the file was changed.
- `docs/briefs/hy4-b2-handback-B2.md` (new) — this hand-back.

`scripts/ops/stack_tags.py` was mutated twice for negative controls and restored byte-for-byte with the edit tool; `cmp` against the `.mine` copy was rc=0 after each restore, and the final full-suite re-run is green. Its `main()` still returns 0 unconditionally: the script's exit behaviour is unchanged.

## Start-state `git status --short`
Saved before any edit in this batch (23:01, branch `w28-s1-delegate`, HEAD `f5b298a`), as the batch 2 master prompt requires for the first hand-back. All 55 entries are known and were left alone.

```
 M .env.example
 M docs/eval/corpus.json
 M scripts/e2e/m07_daemon_drill.py
 M scripts/e2e/w27_denial_drill.py
 M section4/src/harness_orchestrator/loop_guard.py
 M section4/src/harness_orchestrator/service.py
 M section4/tests/unit/test_agent_tree.py
 M section4/tests/unit/test_delegation.py
 M section4/tests/unit/test_lifecycle_recovery.py
 M section4/tests/unit/test_loop_guard.py
 M section4/tests/unit/test_loop_guard_wiring.py
 M section7/app/projects/[projectId]/runs/[runId]/controlApi.ts
 M section7/app/projects/[projectId]/runs/[runId]/page.tsx
 M section7/app/projects/[projectId]/runs/[runId]/runDetailAggregate.tsx
 M section7/app/runs/runsApi.ts
 M section7/src/components/ToolActivity.tsx
 M section7/src/components/WorkingArea.tsx
 M section7/src/lib/deriveView.ts
 M section7/tests/unit/deriveView.test.ts
 M section7/tests/unit/runAggregate.test.ts
 M section7/tests/unit/runDetailPage.test.tsx
 M section7/tests/unit/toolActivity.test.tsx
 M section7/tests/unit/userStatus.test.tsx
?? docs/briefs/evidence/pilot-prep/m01-native-edit.json
?? docs/briefs/hy4-b2-handback-A1.md
?? docs/briefs/hy4-b2-handback-A2.md
?? docs/briefs/hy4-b2-handback-A3.md
?? docs/briefs/hy4-b2-handback-B1.md
?? docs/briefs/hy4-b2-handback-SUMMARY.md
?? docs/briefs/hy4-handback-A1.md
?? docs/briefs/hy4-handback-A2.md
?? docs/briefs/hy4-handback-A3.md
?? docs/briefs/hy4-handback-A4.md
?? docs/briefs/hy4-handback-B1.md
?? docs/briefs/hy4-handback-B2.md
?? docs/briefs/hy4-handback-B3.md
?? docs/briefs/hy4-handback-B4.md
?? docs/briefs/hy4-handback-PROGRESS.md
?? docs/briefs/hy4-handback-SUMMARY.md
?? docs/briefs/hy4-master-prompt-batch2-2026-11-05.md
?? docs/briefs/hy4-master-prompt-batch2b-resume-2026-11-05.md
?? docs/briefs/hy4-master-prompt-batch2c-finish-2026-11-05.md
?? docs/decisions/2026-11-05-delegated-scope-existing-paths.md
?? docs/decisions/2026-11-05-intent-permissions.md
?? docs/runbooks/redeploy-worker-and-orchestrator.md
?? docs/yc/
?? scripts/e2e/m01_native_edit_drill.py
?? scripts/ops/
?? section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx
?? section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.test.tsx
?? section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.tsx
?? section7/src/components/ScopeRequestEditor.test.tsx
?? section7/src/components/ScopeRequestEditor.tsx
?? section7/src/lib/scopeRequest.ts
?? section7/tests/unit/scopeRequestEditorA11y.test.tsx
```

## Commands run

The literal command from the prompt fails on this host because `python3` on PATH is the WorkBuddy-managed 3.13.12 interpreter, which has no pytest. Recorded as a real failure, not tidied out:

```
cd "/Users/user/untitled folder 2/harness" && python3 -m pytest scripts/ops/test_stack_tags.py -q
→ /Users/user/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3: No module named pytest
→ PYTEST_RC=1
```

No workaround for the *test* was invented: the same command with an interpreter that has pytest, and the system `python3` with pytest 8.4.2, both pass.

```
cd "/Users/user/untitled folder 2/harness" && PATH="/Users/user/untitled folder 2/harness/section4/.venv/bin:$PATH" python3 -m pytest scripts/ops/test_stack_tags.py -q
→ ........                                                                 [100%]
→ 8 passed in 0.10s
→ PYTEST_RC=0
```
```
cd "/Users/user/untitled folder 2/harness" && /usr/bin/python3 -m pytest scripts/ops/test_stack_tags.py -q
→ ........                                                                 [100%]
→ 8 passed in 0.10s
→ SYSTEM_PYTEST_RC=0
```
```
cd "/Users/user/untitled folder 2/harness" && make lint > /private/tmp/hy4-b2c/make-lint.txt 2>&1; echo "MAKE_LINT_RC=$?"
→ MAKE_LINT_RC=0
→ == ruff section1 ==
→ cd section1 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
→ All checks passed!
→ == ruff section2 ==
→ cd section2 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
→ All checks passed!
→ == ruff section3 ==
→ cd section3 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
→ All checks passed!
→ == ruff section4 ==
→ cd section4 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
→ All checks passed!
→ == ruff section5 ==
→ cd section5 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
→ All checks passed!
→ == ruff section6 ==
→ cd section6 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
→ All checks passed!
→ == ruff section8 ==
→ cd section8 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
→ All checks passed!
→ == ruff section10 ==
→ cd section10 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
→ All checks passed!
→ == ruff section11 ==
→ cd section11 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
→ All checks passed!
```

Because `make lint` only loops over section1..section11, `scripts/ops/` is not in it; root ruff was run on the two files directly to check the new import:

```
cd "/Users/user/untitled folder 2/harness" && section4/.venv/bin/ruff check --no-cache scripts/ops/stack_tags.py scripts/ops/test_stack_tags.py
→ All checks passed!
→ ROOT_RUFF_RC=0
```

One real read-only run, output pasted verbatim (no `docker compose`, no state change; the script only calls `docker ps`):

```
cd "/Users/user/untitled folder 2/harness" && python3 scripts/ops/stack_tags.py
→ STACK_TAGS_RC=0
→ Stack tag report (read-only: docker ps)
→
→ Running services:
→   alertmanager           prom/alertmanager:v0.28.1                tag=v0.28.1  (harness-alertmanager-1)
→   db                     harness-db-walg:v3.0.9                   tag=v3.0.9  (harness-db-1)
→   events-retention       harness-backend:vw28s6                   tag=vw28s6  (harness-events-retention-1)
→   frontend               harness-frontend:vw24b2                  tag=vw24b2  (harness-frontend-1)
→   github                 harness-backend:vw28s6                   tag=vw28s6  (harness-github-1)
→   harness-ci-scratch-pg  pgvector/pgvector:pg18                   tag=pg18  (harness-ci-scratch-pg)
→   identity               harness-backend:vw28s6                   tag=vw28s6  (harness-identity-1)
→   meeting                harness-backend:vw28s6                   tag=vw28s6  (harness-meeting-1)
→   models                 harness-backend:vw28s6                   tag=vw28s6  (harness-models-1)
→   orchestrator           harness-backend:vw27s3                   tag=vw27s3  (harness-orchestrator-1)
→   prometheus             prom/prometheus:v2.53.0                  tag=v2.53.0  (harness-prometheus-1)
→   review                 harness-backend:vw28s6                   tag=vw28s6  (harness-review-1)
→   sandbox                harness-backend:vw28s6                   tag=vw28s6  (harness-sandbox-1)
→   sandbox-egress         harness-backend:vw28s6                   tag=vw28s6  (harness-sandbox-egress-1)
→   siem-push              harness-backend:vw28s6                   tag=vw28s6  (harness-siem-push-1)
→   sso-idp                quay.io/keycloak/keycloak:26.1           tag=26.1  (harness-sso-idp-1)
→   webhook-delivery       harness-backend:vw28s6                   tag=vw28s6  (harness-webhook-delivery-1)
→   worker-daemon          harness-worker-daemon:vw27s2             tag=vw27s2  (harness-worker-daemon-1)
→
→ Backend services: 11 of 11 expected running
→ Missing: none
→ Agreement: MIXED
→   vw28s6       10 service(s): events-retention, github, identity, meeting, models, review, sandbox, sandbox-egress, siem-push, webhook-delivery
→   vw27s3        1 service(s): orchestrator
→ Worker daemon: harness-worker-daemon:vw27s2 (tag=vw27s2); separate image, not part of the backend group above.
→ Read-only: no container, image, volume or compose state was changed.
```

## Proof the new tests fail without the change

Both controls followed batch 2b amendment 2: `cp` only created new `.mine` files under `/private/tmp/hy4-b2-b2/` (never overwrote a tracked file), each mutation was one targeted edit-tool change, each restore was one edit-tool change, and each restore was proved with `cmp`. Cleanup was allowed, so the two `.mine` copies were removed after the proofs (`rm -f ...` rc=0).

```
cd "/Users/user/untitled folder 2/harness" && mkdir -p /private/tmp/hy4-b2-b2 && cp scripts/ops/stack_tags.py /private/tmp/hy4-b2-b2/stack_tags.mine && cp scripts/ops/test_stack_tags.py /private/tmp/hy4-b2-b2/test_stack_tags.mine
→ (new files created; git diff --stat -- scripts/ops/... empty before controls because scripts/ops/ is untracked at HEAD)
```

**Control 1 — one service name removed from `EXPECTED_BACKEND_SERVICES`.** Removed the line `"siem-push",` in `scripts/ops/stack_tags.py` with the edit tool, then:

```
cd "/Users/user/untitled folder 2/harness" && PATH="/Users/user/untitled folder 2/harness/section4/.venv/bin:$PATH" python3 -m pytest scripts/ops/test_stack_tags.py -q -k expected_backend_services_match_the_compose_file
→ F                                                                        [100%]
→ E       AssertionError: EXPECTED_BACKEND_SERVICES drifts from deploy/docker-compose.yml:
→ E           in script but not compose: set()
→ E           in compose but not script: {'siem-push'}
→ E         Update EXPECTED_BACKEND_SERVICES in stack_tags.py to match.
→ E         Extra items in the left set:
→ E         'siem-push'
→ scripts/ops/test_stack_tags.py:158: AssertionError
→ 1 failed, 7 deselected in 0.16s
→ NEG_CONTROL_1_RC=1
```

Restored with the edit tool; `cmp scripts/ops/stack_tags.py /private/tmp/hy4-b2-b2/stack_tags.mine` printed nothing (rc=0), `git diff --stat -- scripts/ops/stack_tags.py` stayed empty (same as before the control).

**Control 2 — the test pointed at a compose service that does not use the `harness-backend` image.** With the edit tool, changed the parser's marker in `scripts/ops/test_stack_tags.py` from `if stripped == "<<: *backend-build":` to `if stripped == "<<: *backend-build" or current == "frontend":`. `frontend` uses `harness-frontend:v${HARNESS_IMAGE_TAG:-dev-local}` (`deploy/docker-compose.yml:645`), has `restart: unless-stopped` and no `profiles:` gate, so this names exactly one non-backend service:

```
cd "/Users/user/untitled folder 2/harness" && PATH="/Users/user/untitled folder 2/harness/section4/.venv/bin:$PATH" python3 -m pytest scripts/ops/test_stack_tags.py -q -k expected_backend_services_match_the_compose_file
→ F                                                                        [100%]
→ E       AssertionError: EXPECTED_BACKEND_SERVICES drifts from deploy/docker-compose.yml:
→ E           in script but not compose: set()
→ E           in compose but not script: {'frontend'}
→ E         Update EXPECTED_BACKEND_SERVICES in stack_tags.py to match.
→ E         Extra items in the left set:
→ E         'frontend'
→ scripts/ops/test_stack_tags.py:158: AssertionError
→ 1 failed, 7 deselected in 0.13s
→ NEG_CONTROL_2_RC=1
```

**Restore proof for both files and the green re-run:**

```
cd "/Users/user/untitled folder 2/harness" && cmp scripts/ops/test_stack_tags.py /private/tmp/hy4-b2-b2/test_stack_tags.mine; echo "CMP_RC=$?"
→ CMP_RC=0
cd "/Users/user/untitled folder 2/harness" && cmp scripts/ops/stack_tags.py /private/tmp/hy4-b2-b2/stack_tags.mine; echo "CMP_RC=$?"
→ CMP_RC=0
cd "/Users/user/untitled folder 2/harness" && PATH="/Users/user/untitled folder 2/harness/section4/.venv/bin:$PATH" python3 -m pytest scripts/ops/test_stack_tags.py -q
→ ........                                                                 [100%]
→ 8 passed in 0.10s
→ PYTEST_RC=0
```

The 7 original tests plus `test_expected_backend_services_match_the_compose_file` all pass again. No mutated file remains in the tree.

## What is NOT verified
- **The first version of B2 failed at collection** (`scripts/ops/test_stack_tags.py:108: NameError: name 'Path' is not defined`) and I did not catch it myself: the reviewer found it and told me in the 2c prompt. This hand-back is the fix for that, not evidence that my earlier run was green.
- The literal `python3 -m pytest scripts/ops/test_stack_tags.py -q` returns rc=1 on this host (`No module named pytest`) because of the WorkBuddy-managed interpreter on PATH. `NEEDS-REVIEWER-RUN` on the reviewer's own shell: `cd "/Users/user/untitled folder 2/harness" && python3 -m pytest scripts/ops/test_stack_tags.py -q`, expected `8 passed`, rc=0. It passed with `/usr/bin/python3` and with `section4/.venv/bin` on PATH here.
- `make lint` does not lint `scripts/ops/` (it loops over section1..section11 only). I therefore also ran root ruff on the two files directly: `All checks passed!`, rc=0, so the new import is not an I001 finding. A reviewer may still want their own `make lint` run on their host.
- No full test suite, no `make static-gates`, no `docker compose` command, no build, no deploy, no paid or hosted model call. Nothing from batch 2 is deployed; all of it is uncommitted in the working tree.
- The live `MIXED` tag state (10 × `vw28s6`, `orchestrator` on `vw27s3`) is an existing deployment condition read from `docker ps`, not a result of this change.

## Unrelated findings
- `scripts/ops/` is untracked at HEAD, so `git diff --stat` cannot show drift in these two files; `cmp` against the `.mine` copies was the meaningful restore proof.
- The script still exits 0 when `docker ps` fails (`stack_tags.py:100-108`). Batch 1 B2 flagged this as a reviewer decision; it is deliberately unchanged here.

## Risks the reviewer should check first
- The compose parse is line-based by design (no PyYAML dependency): it counts services merging `<<: *backend-build` and drops any with `profiles:` or `restart: … no`. If the compose file starts expressing backend services another way, this test will drift silently. A YAML parse would be sturdier but adds a dependency.
- Control 2 mutated the test's marker to name `frontend`; it did not change the compose file. It proves the assertion catches an extra non-backend service, not that the parser is correct for every future compose shape.
- Whether `stack_tags.py` should exit non-zero when `docker ps` fails remains the reviewer's ruling; exit behaviour was not touched.

## Scratch files
- `/private/tmp/hy4-b2-b2/` — created empty; both `.mine` copies were deleted after the `cmp` proofs (`rm -f` rc=0).
- `/private/tmp/hy4-b2c/` — `git-status-baseline.txt`, `git-status-after-b2.txt`, `make-lint.txt`, `stack-tags-run.txt` (logs and baseline for this batch), left in place for the reviewer.

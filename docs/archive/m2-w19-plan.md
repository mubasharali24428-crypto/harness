# Marathon 2.0 W19 — defect buffer and release hardening

The 2026-10-28 Marathon 2.0 roadmap reserves W19 for evaluation regressions,
operator documentation, rehearsals and high-risk boundary rechecks. Its exit
requires open blockers to have an owner and mitigation, with no silently waived
gate; external reviews and Hermes rights are tracked separately. This is a
working record, not a release or W19-completion decision.

## Work completed independently (2026-10-30)

### Section 11 indexing test regression

The reported `7 == 8` failure in `test_index_lifecycle_over_http` was reproduced
deterministically: force a real authenticated HTTP request after the first
chunk upsert and before the index transaction commits. With the old in-memory
SQLite fixture, that request's authentication session shares the writer's
single connection; its session close rolls back the first row, while the job
continues counting it. The new regression test observes exactly 7 stored versus
8 reported before the fix.

The Section 11 test fixture now uses a temporary file-backed SQLite database,
so concurrent sessions use independent connections. The same forced interleave
then preserves all indexed rows. The HTTP test also uses a monotonic ten-second
deadline and reports the last job status on failure, rather than relying on 100
polls completing within about one second under load.

`chunksIndexed` remains the number of chunks processed in this indexing pass:
unchanged files still report zero. It is not changed to total stored rows, which
would alter existing incremental semantics. This finding establishes a concrete
test-fixture cause for the observed mismatch; it does not prove that all
possible production PostgreSQL or multi-process indexing races are absent.

Validation: `section11/.venv/bin/python -m pytest tests/ -q` **51 passed,
1 skipped**, and Section 11 Ruff passed. The targeted regression was observed
failing before the fixture fix and passing after it. The skipped PostgreSQL
test was then run separately against its own disposable pgvector container:
**1 passed**, covering migrations and vector search, but not the forced
concurrent-auth interleave or multiple service processes.

### Local boundary recheck

Section 5's shell gate, web gate, egress allow-list and policy tests: **41 passed,
5 skipped** because OS confinement is unavailable on this host. These are local
tests, not a replacement for the deployed worker,
network confinement or failure-injection rehearsals. Skips remain open evidence,
not passes.

The evaluation corpus structure check passed: 39 tasks, 6 held out, 29 marked
implemented. This checks the corpus contract; it does not execute the tasks.

### Index admission race (current W19 work)

Two `start_index` requests for the same project could both pass the old
`lock.locked()` check while the first awaited its tier check. The service now
reserves the project before that await and releases it on admission failure,
cancellation, or job completion. A controlled interleaving reproduces the old
race and checks rejection and later readmission; additional tests check release
on cancelled admission and after an indexing failure. This reservation is
**per process**; it is not a distributed lock and does not prove replica-wide
exclusion.

> Resolved 2026-10-31 (commit `cb88801`): admission is now a **durable**
> reservation — migration `0072 project_index_reservations` plus one atomic lease
> upsert (`INSERT … ON CONFLICT DO UPDATE … WHERE lease expired RETURNING`), so
> the exclusion spans processes. Proven two ways: two `ContextService` instances
> over one shared DB (second refused / expired lease reclaimed / released on
> completion) and on live Postgres-18 (first acquire `INSERT 0 1`, concurrent
> second refused `INSERT 0 0`, owner unchanged). See
> [`section11/tests/test_w19_replica_admission.py`](../section11/tests/test_w19_replica_admission.py).
> Cross-replica event-bus **delivery** (Postgres NOTIFY) is also now proven
> (`4b22ede`, `docs/briefs/evidence/w19/replica-notify-*.json`).

After this change the Section 11 suite passed **54 tests, 1 skipped**; Ruff and
`make static-gates` passed. The separately executed pgvector migration/vector test remains the
earlier proof described above, not a distributed admission test.

### Independent W18 importer review and fixes

The W18 slices are now committed at `485ee29`; their focused importer, example
and pricing checks passed **24 tests**. Manual synthetic repros found two
defects in `scripts/migrate_agent_config.py` despite those tests:

* A `BEGIN ... PRIVATE KEY` without a matching end marker dropped the begin line
  but retained the body. The importer now drops through the matching end marker
  or end of file. The guide states that its filter is best effort and users must
  remove secrets and review output: a heuristic cannot detect every credential.
* The size check adds the entire existing document to the replacement block
  before finding the old fenced block. Replacing an existing ~40 KB block with
  a ~40 KB block was refused even though the result fits under 64 KB. The size
  check now measures the actual merged text, and malformed or repeated existing
  markers are refused. Regression tests cover both failures.

The focused importer, example and pricing suites passed **38 tests** after the
fix. The current guide also warns that a concurrent rules edit between GET and
PUT can be overwritten; the API does not provide a conditional write here.

The disposable PostgreSQL suite passed **2 tests**, including a forced read
from a distinct backend connection during an index transaction; every indexed
row remained after commit. This is not a cross-replica exclusion test.

The fixed `skills-governance` evaluation was rerun against a temporary local
store: **PASS**, with the other 38 corpus tasks deferred. Its Docker-backed
revision check matched the three checked backend files and two of three worker
files, but the worker image's `engine.py` hash differed from the worktree.
The evaluation is therefore not current-deployment evidence. See
[`w19-skills-eval-verified.json`](briefs/evidence/w19-skills-eval-verified.json);
the name records that the check ran, not that revision verification passed.
The other local boundary recheck passed **91 tests, 2 skipped**; skips were not
treated as passes.

The W18 plan was corrected to describe the actual merged rules-document importer,
its sources and its GET/PUT concurrent-edit limitation. The user reported that older images returned
`schema-drift` / readiness 503 after migration 0071; recreating all backend
services on the current image restored readiness 200. That is user-reported
deployment state, not an independent W19 readiness test.

## Open gates and coordination

Owners below are responsible roles from the roadmap or affected area, not
named assignments. A human must assign a person before release. Mitigations do
not waive the gate.

| Open item | Responsible role | Current mitigation and when to resume |
|---|---|---|
| W17 load, real-provider and process failure evidence; 100-user dry-run latency exceeded the proposed control-plane objective | Reliability / operator | Keep capacity and SLO claims qualified. Continue after W17's owner finishes its active changes, using a separately identified environment and an agreed provider budget. |
| W18 release-candidate review | W18 / release owner | Importer defects were fixed locally; credential filtering is heuristic and concurrent writes remain unprotected. Finish independent review after the other agent's current work. |
| PostgreSQL indexing and multi-process race evidence | Section 11 / database owner | A separate-session read during indexing passed against disposable Postgres; cross-replica admission is not enforced or proven and remains open. |
| Full fixed and held-out evaluation, live journey and high-risk deployed rehearsals | Evaluation / security / release owners | Preserve skipped and failed outcomes. Run after the deployment and fixture revision are fixed and shared-stack work is finished; obtain the model/spend budget where applicable. |
| Worker image `engine.py` differs from the checked worktree | Deployment / worker owner | Do not treat current evaluation results as matching the deployed revision. Rebuild and verify the worker image at a coordinated maintenance point, then repeat revision-sensitive evaluations. |
| External security review and counsel | Security lead / qualified counsel | Track separately from internal tests; engage the reviewers and resolve the legal placeholders before relevant commercial claims. |
| Hermes rights and distribution | Rights holder / legal owner | The W6 audit records unresolved university/teammate rights evidence. Do not treat a demonstration or code test as rights clearance; revisit when written evidence is available. |
| Operational ownership, status communications and backup monitoring | Operator / incident owner | W17's proposed runbook is guidance, not an assigned roster or proven alert route. Resume after the operator and target deployment are identified. |

The current W17 and W18 work uses a shared tree and deployment. The importer
fixes touched committed W18 files that were clean when work began; the other
agent's active files and the shared stack were not modified or restarted.

# Week 8 plan — parallelism and worktrees (W8)

Follows [Week 7](week-7-plan.md). Governing rule inherited from
[the marathon execution plan](marathon-execution-plan.md): *"An unchecked gate
stays open when a dependent week begins."*

## Position

W4 is closed (all four claims + the MCP mechanics, third-party leg named as
remaining). W6 is closed (CLI, chat, local-workspace mode). W7's editor half is
closed and its LSP half is **blocked on a prerequisite this repo does not
contain** (Hermes/H0–H2), recorded rather than hidden.

W8 has no open dependency: runs are already claimed by a queue, bounded by an
executor, and each gets its own workspace and container boundary. What W8 must
do is *prove that under real concurrency* — and fix whatever the proof exposes,
because an isolation claim that has only ever been tested with one run alive at
a time is not a tested claim.

## Gate

Roadmap, verbatim (`docs/roadmap-2026-10-25.md:145-152`):

> Per-run git worktrees so N runs touch the same repo without colliding; a real
> queue UI with priorities; wire the **agent tree to actual execution** (built,
> and its own docstring says nothing calls it today).
>
> **Gate:** three concurrent runs in one repo, each isolated, each reviewable,
> none clobbering another's checkpoint.

Marathon exit evidence (`docs/marathon-execution-plan.md:159`): *"Three
concurrent runs do not share writable state or overwrite checkpoints; cleanup
handles crashes."*

Expanded into claims that must be shown, not asserted:

1. three runs claimed at the same time, on the same project, hold **distinct**
   writable state (workspace dir, session root, boundary names) — observed
   during execution, not inferred from code;
2. each is **reviewable**: each run's recorded changes and per-file digests
   belong to that run, and a decision on one cannot read as a decision on
   another;
3. **no checkpoint clobbering**: run A's manifests and run B's manifests are
   separate, and the shared blob store cannot make one run read the other's
   state;
4. **cleanup handles crashes**: killing a worker mid-run releases its claim,
   removes its boundary (network + relay), and leaves nothing that must be
   reclaimed by hand;
5. the roadmap's other two W8 clauses are either done or **explicitly not done
   in this week** — no silent dropping.

## What exists today (audited 2026-10-27)

| Piece | State | Where | What is missing |
|---|---|---|---|
| Atomic claim | **exists, enforced in code** | `run_queue.py:161-205` — `UPDATE … WHERE run_id = (SELECT …) RETURNING`, `FOR UPDATE OF rc SKIP LOCKED` (`:144-158`), plus `pg_advisory_xact_lock(7270001)` (`:193`) because the per-team `COUNT` cap overshoots under READ COMMITTED | none for the claim itself; the cap race was measured, not assumed (`unit/test_claim_cap_live_pg.py`) |
| Per-team running cap | **exists** `HARNESS_RUN_CONCURRENCY`=4, enforced inside the claim SQL (`run_queue.py:259-262`) | `services.py:400` → `service.py:221` | — |
| Engine executor bound | **exists** `ENGINE_MAX_CONCURRENCY`=8 threads (`engine_runtime.py:308-310,61,104-107`) | `docs/engine-concurrency.md` pins the equation `8 = cap × teams` | the bound is per-process; two orchestrator replicas each get 8 (documented, but worth restating in the evidence) |
| Per-run workspace/session | **exists** | `engine.py:472-487` (`run_workspace()` = `<root>/<session_id>` + escape check), session/tmp `:510-517` | never observed with 3 runs alive at once |
| Worker daemon concurrency | **exists** `ThreadingHTTPServer` (`worker_daemon.py:156-158`); `_LOCK` guards only the registry (`:124-128`); duplicate run id → 409 | paths come from the daemon's own `DSH_WORKSPACE` (`:6-8`) | **no cap inside the daemon** — admission is entirely the queue's |
| Per-run boundary | **exists** uuid-named `harness-<32hex>[-net|-relay]` (`container_runtime.py:48-50`), internal gateway-less network (`:75`), relay (`:81-112`), egress attach (`:55-72`), `close()` disconnects the proxy before removing the network (`:161-177`) | shared: the egress proxy container and the `harness_runs` volume | if the owning process dies before `close()`, the **run network leaks** — the class that cost eight days in W4, now only partially healed |
| Checkpoints | **exists, per-run manifests** `<DSH_WORKSPACE>/.checkpoints/<run_id>/<seq>-<label>.json` (`run_checkpoints.py:69-74,102-104`), id validated (`:85-88`) | one run cannot overwrite another's manifest | **`blobs/` is shared and has no reaper** (`:244-248` says blobs are "left for the next reaper" that does not exist); concurrent same-digest writes reuse one `.tmp` name (`:127-129`) |
| Stop/cancel truthfulness | **exists** | `service.py:1818` → `_cancel_live_task` (`:1931`), `_kill_engine` (`:1907-1913`), `engine_quiesced` gate (`lifecycle.py:145-174`), `docker rm --force` until `confirmed_gone` (`container_runtime.py:134-159`) | the crash path (worker killed by the runtime, not by a stop) is unproven for boundary removal |
| Reaper | **exists** `release_timed_out_claims` (`run_queue.py:111-141`) → BLOCKED/`heartbeat_lost` (`service.py:2318-2349`); `reclaim_orphans` (`:351`) | releases claims | does **not** sweep leaked networks or orphan blobs |
| Git worktrees | **absent** | `docs/artifacts.md:45` states "Worktree **not used**… one directory per run id"; no worktree code exists anywhere in the repo | see decision 1 |
| Three-concurrent-runs evidence | **absent** | tests cover claims (`test_claim_batching.py`), caps (`unit/test_configurable_queue_caps.py`), isolation-with-one-run (`unit/test_run_isolation_and_stop_truth.py`), agent-tree races (`unit/test_agent_tree_races.py`) | **no test or drill has ever run three concurrent runs in one repo** |

## Decisions owed before code

1. **Worktrees vs per-run directories.** The roadmap asks for per-run git
   worktrees so N runs touch *the same repo*. Today a run gets a **copy** of a
   seeded workspace (`engine.py` seeding), which is already collision-free — and
   it is why "same repo" has no shared history or index to collide with.
   Worktrees would buy a shared object store and real repo context (the thing
   gap-list item B4 says the agent cannot see: `.git` is stripped), at the cost
   of runs being able to reach the repo's other refs and the object store — a
   confinement question, not a convenience one. **Position:** W8 proves the
   gate with the existing per-run directories, and treats worktrees as a
   separate decision requiring its own security change + enforcement, per the
   standing ground rule. If worktrees get built this week, it is because the
   drill showed a real collision, not because the roadmap sentence was there.
2. **Queue UI with priorities** — frontend + contracts surface (a `priority`
   field is not in the frozen schema). **Position: not in W8's gate.** Recorded
   here so the week's scope is honest rather than quietly narrowed.
3. **Agent tree wired to execution** — the audit shows
   `unit/test_agent_tree_races.py` and spawn endpoints exist; the roadmap's own
   complaint is that nothing calls the tree. **Position: a separate slice,
   gated by whether the tree changes the isolation claims** (a child agent
   sharing its parent's workspace is exactly a "shared writable state" case).
4. **What a crash must prove.** Killing a worker container is not the same as
   `stop_agent`: no one asked for a stop. The claim to show is that the system
   converges without a human: claim released, boundary gone, other runs
   unaffected, and the crashed run's partial state visible as what it is.

## Slices, in order

1. **Three concurrent runs, live.** Same project, three goals, all claimed
   while all three are executing; observe during execution (not after):
   distinct workspace paths, distinct boundary names, distinct session roots;
   after completion: each run's `/changes` and digests belong to it, and each
   run's checkpoint manifests are its own. Evidence names the model, the mode
   (`container`) and the caps in force, because "3 concurrent" only means
   something relative to them.
2. **Crash cleanup.** Kill one worker mid-run. Assert: its claim is released or
   reaped (never silently held), its boundary is gone (no leaked
   `harness-<hex>-net`), the other two runs finish untouched. If anything leaks,
   fix the sweeper and pin it with a test — the W4 leak is the precedent for
   what an un-swept network costs.
3. **Shared blob store.** Prove or refuse: a second run cannot read or replace
   another's checkpoint content through `blobs/`; fix the same-digest `.tmp`
   naming race; add the blob reaper the purge path already assumes exists.
4. **Worktree decision, executed as decided** (see 1) — either build it with
   its confinement change, or record in `docs/artifacts.md` and the gap list
   that the gate was met without it and why.

## Known constraints

- The stack's model is local and slow (a single tool turn can take ~7 minutes);
  three concurrent runs are the realistic maximum for a live drill, and the
  per-run deadline must leave room for all three (`ENGINE_RUN_TIMEOUT_SECONDS`,
  `HARNESS_RUN_CONCURRENCY`, `ENGINE_MAX_CONCURRENCY` all named in evidence).
- Two orchestrator replicas each get their own executor bound; a drill run
  against one replica does not evidence multi-replica behaviour, and the
  evidence file will say so.
- SQLite claims rely on single-writer reality (`run_queue.py:170-173`); the
  concurrency proof is taken on Postgres, which is what the compose stack runs.

## Slice 2 outcome (2026-10-27) — code landed, live gate blocked

Three real defects were found while building the crash drill, and all three are
fixed with tests. None of them was visible in the code review; each needed a
live stack.

**1. A container-mode run could not write anything at all.** Every `write` the
agent attempted returned `EACCES: permission denied, mkdir '/var/lib/
harness-runs/workspace/<run>'`, the run settled `blocked` with zero changes, and
no drill could show a reviewable diff. Root cause, measured on the stack:

| observation | evidence |
|---|---|
| worker launched as uid 10001, directory owned root:root 0755 | `ls -ln` in the daemon + the worker's own `os.stat` result in the audit |
| `_worker_user()` falls back to `10001:10001` for a root-owned dir | `confine.py:256-259` — the fallback and the directory disagreed |
| launcher chown fixed it while the dir was the *only* thing in play | after a manual `chown 10001` the same run's writes returned `ok=true` |
| ... but Docker undoes it at mount time | `pre 10001 → during 0`, `pre 0o2777 → during 0o755`: a `volume-subpath` mount is re-created as root:root 0755 by the engine |

So the durable fix is in `confine._mount()`: under `HARNESS_RUNS_ROOT` the mount
becomes a **bind of the named volume's own engine-host directory**
(`<Mountpoint>/workspace/<run>`), which Docker does not re-ownership, instead of
`volume-subpath`. Verified before it went in: `WRITE OK`, file created as uid
10001, directory left 10001:10001. `volume-subpath` stays as the fallback when
the mountpoint is unresolvable, and `HARNESS_RUNS_BIND=0` forces it. The
launcher still hands the directories over first
(`confine.claim_run_dirs_for_worker`, with a warning when a path it should own
is missing) so the two halves are observable in the daemon log:
`handed 3 run path(s) to uid 10001 before launching the worker`.

**2. The boundary sweeper's "is it running?" check never fired.**
`_running_containers` parsed `{{.ID}} {{.Names}} {{.State}}` looking for the
substring `" running "`, which cannot match a line that *ends* with the state —
so every container looked stopped. On a deletion path that is a licence to
remove a live run's network. The format is now piped
(`{{.ID}}|{{.Names}}|{{.State}}`), parsed positionally, and pinned by
`test_a_running_container_is_recognised_even_with_a_spaced_name`.

**3. `agent_runs.state` is not the durable lifecycle.** The sweep initially asked
that column for terminal runs; the table only ever holds `idle`/`pr_opened`,
because the orchestrator's states are replayed from `agent_events`. Settlement
now comes from the event log itself — any event at or after `engine_completed`
means no worker can still hold the boundary (`service._settled_run_ids`),
pinned against two replicas over one store.

The sweep's own rules (labelled, older than the grace window, not held) and its
two proofs are in `container_runtime.sweep_orphan_boundaries`, with
`test_boundary_sweep.py` (9 tests) refusing every deletion the code is not
entitled to make: a held run, an unlabelled network, an unknown run whose
container is still up, a young network, and the shared egress proxy — which is
disconnected, never stopped, because it serves the other runs too.
`worker.py` runs the DB-backed sweep on its own slow cadence
(`BOUNDARY_SWEEP_INTERVAL_SECONDS`, default 600), and the socket-holding daemon
runs the DB-free weak-rule pass (`HARNESS_BOUNDARY_SWEEP_SECONDS`,
default 600) at the same time. Boundary networks are now labelled
`harness-run=<run id>` at creation, which is what makes an orphan traceable at
all.

**Blocked, and not by this code.** The live gates for slices 1 and 2 need the
stack's model, and `qwen-local:latest` is gone from the host's Ollama:
`/api/tags` returns only `nomic-embed-text`, `engine_failed: model
'qwen-local:latest' not found` ends every run in seconds, and the last four
drill attempts (17:30) failed that way. The blobs (~6.4 GB) are still in
`~/.ollama/models`, but the tag manifest is not, so the model cannot be
re-created faithfully without its original Modelfile — and guessing a chat
template would produce a different model than the one every earlier evidence
file was taken against. **Resume point:** restore `qwen-local:latest` (same
weights, same template), re-run `scripts/e2e/w8_concurrency_drill.py`
(compose MUST be driven with `-f deploy/docker-compose.yml --env-file .env` from
the repo root, or `HARNESS_MASTER_KEY` arrives empty and the orchestrator
crash-loops), then `scripts/e2e/w8_crash_cleanup_drill.py`, which is written and
compiles but has never been run.

What the drills already prove without a verdict: three `executing` runs at once
with three distinct boundary networks (`maxConcurrent: 3`, `nets: 3`,
`distinctWorkerNetworkSets: 3` in `w8-concurrency-*.json`), one run dir per
worker, and the crash path — killing the daemon released all three stale claims
and moved each run to `blocked` honestly, with no boundary left behind this time
(the daemon's own `close()` on SIGTERM cleaned up; the leaks in `1793119753`
were pre-label images and were removed by hand).

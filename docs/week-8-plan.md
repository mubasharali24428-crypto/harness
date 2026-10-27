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


## Slice 1 + 2 outcome (2026-10-27) — both live gates PASS

`w8-concurrency-1793123190.json` (verdict PASS, 10/10 checks) and
`w8-crash-cleanup-1793123310.json` (verdict PASS, 11/11 checks), both driven
against the running compose stack with the model named in the evidence.

| gate claim | how it is shown |
|---|---|
| three runs claimed at the same time, same project | state timeline has one entry with all three `executing`; `maxConcurrentExecuting: 3` |
| distinct writable state, observed during execution | three distinct boundary networks, one run directory per worker, no directory shared between two workers |
| each reviewable | each run recorded exactly one change with its own digest (`dl/043/243…`), and a wrong-shaped review is what the W7 drill already pins as 409 |
| none clobbering another's checkpoint | every run's manifests contain only its own file; `foreignCheckpointNames` empty for all three |
| crash: claim released or reaped, never silently held | victim's client SIGKILLed mid-run → run `blocked`, `run_claims` row gone, `engine_failed` in the audit |
| crash: boundary gone, neighbours untouched | a launcher-shaped orphan (network + relay + still-running container, no owner) removed by the sweep; the live neighbour was in `kept`, none of its containers stopped |

### What the proof actually cost: five defects, none visible in review

1. **A container-mode run could not write anything.** Every `write` returned
   `EACCES: permission denied, mkdir '/var/lib/harness-runs/workspace/<run>'`,
   the run settled `blocked` with zero changes, and no drain could show a diff.
   The worker runs as the run directory's owner, and for a root-owned directory
   as uid 10001 — inside a root:root 0755 directory. Handing the directories
   over was necessary but not sufficient: **Docker re-creates a `volume-subpath`
   mount as root:root at mount time** (`pre 10001 → during 0`; a pre-set
   `0o2777` came back `0755`). Fixed by binding the named volume's own
   engine-host directory instead (verified: `WRITE OK`, directory left
   10001:10001), with `volume-subpath` kept as the fallback and
   `HARNESS_RUNS_BIND=0` to force it.
2. **Taking *every* directory was the opposite bug.** Once writes worked, the
   orchestrator's staging pass — which runs as the provisioning uid and must read
   what the agent wrote — failed with `Permission denied` on the file the agent
   had just created, because the handover had moved the tree to 10001. The
   handover now claims only root-owned directories and leaves a provisioned one
   to the uid that provisioned it.
3. **The sweeper could not see a running container.** `_running_containers`
   matched `" running "` against a line that ends with the state, so on a
   deletion path every container looked stopped. Listing is piped and parsed
   positionally now.
4. **`agent_runs.state` is not the durable lifecycle.** Settlement is decided
   from `agent_events` (at or after `engine_completed`), not that column, which
   only ever holds `idle`/`pr_opened`.
5. **The drill measured itself wrong twice.** It looked for a mount destination
   of `/workspace` (the real ones are `/var/lib/harness-runs/workspace/<run>`,
   and a `volume-subpath` mount reports no `RW`), and its checkpoint reader
   assumed one-line JSON while the product writes these manifests pretty-printed
   — reporting "0 checkpoints" for a run that had two.

Sweeps are also given the rules the deletion deserves: a network is removed only
when it is labelled, older than the grace window, not held by this process, and
either durably settled or attached to nothing that is running. An unlabelled or
unproven network is kept, and the shared egress proxy is disconnected, never
stopped, because it serves the runs beside this one. Boundary networks are
labelled `harness-run=<run id>` at creation, which is what makes an orphan
traceable at all.

### The model, and why the evidence names it

The stack's local `qwen-local:latest` disappeared from the host's Ollama
(`/api/tags` listed only `nomic-embed-text`; ~6.4 GB of blobs remained, the tag
manifest did not), so every run failed in seconds with
`engine_failed: model 'qwen-local:latest' not found`. These two gates were taken
against **`deepseek/deepseek-v4-flash` through OpenRouter** — a cheap,
tool-capable model at $0.05/$0.09 per million tokens — wired exactly as the
committed patch documents (`deploy/patches/openrouter.patch.yml`, baked into the
worker image, `DEEPSEEK_BASE_URL=https://openrouter.ai/api/v1`,
`DSH_MODEL` set, key in the gitignored `.env`). The provider is part of the
evidence, not a footnote: a faster model changes timing, so it changes what the
concurrency window looks like, and the whole drill loop now finishes in about a
minute instead of half an hour.

Compose must be driven with `-f deploy/docker-compose.yml --env-file .env` from
the repo root; without the explicit file compose does not find the config, and
without `--env-file` `HARNESS_MASTER_KEY` arrives empty and the orchestrator
crash-loops.

**Still open after this week** (recorded, not hidden): the worktree decision of
slice 4 is answered by construction (per-run directories already isolate runs)
and is written up in `docs/artifacts.md`; a queue-priority UI and wiring the
agent tree to execution remain out of scope by decision 2; and the local Ollama
model is gone until someone reinstates it, which no longer blocks the harness
because the provider is configuration.

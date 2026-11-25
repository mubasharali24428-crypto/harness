# X-09 / S-02 — stable executor prompt prefix (2026-11-25)

Branch `w28-s3-memory-prefix` off `w28-s2-sprints1-4` at `fe2233c`. States follow
`FUTURE PLAN 2.0.md` §0. This is **Track 4** of the hybrid-memory kickoff: the prefix
discipline that the memory layers (A: W12 project memory, B: case memory, C: the X-12
candidate store) depend on for measured cache wins. Cloud evidence only; no owner-Mac
or live-provider numbers yet.

## Prompt layout the executor now receives

```
[ frozen prefix                              ] [ per-run  ] [ per-attempt, volatile        ]
  rules -> skills -> project memory -> @refs     plan.goal    guidance, repo-context baseline
```

- **Frozen at run start.** `section4/src/harness_orchestrator/prompt_prefix.py` renders
  the four advisory blocks once, at the run's first execution attempt, and every later
  attempt (resume, leader-approval retry, guidance turn) reuses the same bytes, even if
  rules, skills or memory are edited mid-run. `propose_plan` clears the snapshot (a new
  plan is a new run start).
- **Volatile last.** The prefix reads only `rules_prompt_block`, `skills_prompt_block`,
  `memory_prompt_block` and `context_bundle.rendered`. Human guidance stays after
  `plan.goal`; the repo-context baseline (git status, tree) is appended by the engine
  after that, once per session (`engine._with_repo_context`, unchanged).
- **Total order in loaders.** Memory `ORDER BY created_at, id`; skills
  `ORDER BY name, version`, so same-instant rows render identically every run.
- **Digest, not text.** The engine result gains `promptPrefix: {sha256, bytes, blocks}`
  only when the prefix is non-empty; runs without advisory context keep their exact
  result shape.

## Evidence

| Piece | State | Evidence |
|---|---|---|
| Frozen snapshot + volatile-last executor goal | **Verified (unit)** | `section4/tests/unit/test_prompt_prefix.py` 15/15 |
| X-09 CI guard: fails if volatile data enters the prefix, if the prefix moves between attempts, or if per-attempt data is placed before it | **Verified (unit)** | Mutation check: swapping guidance in front of the prefix fails 2 tests; removing the freeze fails 3 tests |
| Memory tie-break order | **Verified (unit)** | `test_memory_loader.py::test_same_instant_notes_render_in_id_order` (fails without `, id`) |
| Existing W12 / N03 / case-memory behaviour unchanged | **Verified (unit)** | memory, skills, rules, N03, case memory, similar cases suites green |
| S-01 cache-read split in `scripts/eval/bench_stats.py` (input vs cache-read tokens, ratio, prefix-digest stability, MIN_N claim rule) | **Verified (unit)** | `scripts/eval/test_bench_split.py` + `test_w7_bench.py` + `test_bench_leaderboard.py` 48/48 |
| Cache-read tokens and TTFT before/after on the eval corpus | **Not run** | Needs live trials (owner-gated spend) |

## Honest limits

- **System prompt and tool schemas are not in this repo.** They come from the pinned
  DeepSeek Harness SDK profile (`includeRuntimeContext:false` already keeps the runtime's
  own volatile context out). Their stability is not tested here.
- **The prefix sits in the goal string, not a separate system block.** It is the first
  bytes of the first user message, which is what a prefix cache keys on, but no
  provider `cache_control` breakpoint is set; the SDK owns the request shape.
- **Per-replica.** The snapshot lives on the in-process run dict, like the blocks it is
  built from. A resume on a different replica has neither (pre-existing limit).
- **Cache numbers are unmeasured.** `bench_trials.py` now copies `usage` and the
  `promptPrefix` digest from driver evidence when present, but no driver prints the
  digest yet and the durable `ENGINE_COMPLETED` event carries no usage. Before/after
  needs live runs.
- Boundaries unchanged (CD-02): no new write path, no new prompt content, no
  auto-promotion, no geometry in prompt bytes, no `contracts/` or migration change.

## Next

1. Owner Mac: `make gate` and a 5-trial live batch to get the first cache-read ratio.
2. Have the eval drivers print `promptPrefix` from `GET /runs/{id}/engine-result` so
   `distinctPrefixDigests` is populated.
3. Then X-12 (Track 3) can be judged against a measured prefix baseline.

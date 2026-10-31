# Stage-1 cleanup — coherent single-revision build (resolves the mixed-image residual)

Taken 2026-10-31 at revision **`5e165fd`**. This closes the one item from the
Stage-0 residual that is genuinely resolvable by engineering without overwriting
the concurrent lane or inventing a human decision: the deployed set was
**mixed** (`vw22orphan1` / `vw22fix4` / `vw22fix3`) with **no coherent
single-revision build**. It is now built, deployed, provenance-checked, and
re-measured. The rest of the quoted residual is explicitly NOT resolved here,
and the reasons are given below — that is the honest state, not a gap in effort.

## What changed

The concurrent lane had, in the meantime, **committed** the durable-reaper /
orphan-window fix (`f1016e2 W22: close the orphan window`) and a sibling of my
admission fix (`0afee0f`), so a coherent build could now include the recovery
work rather than revert it.

- Built one image set from a clean `git archive 5e165fd` → tag `vs1coherent`,
  covering **backend, engine-worker, worker-daemon, frontend** together.
- Provenance verified: in-image `worker.py` `md5 2490b95f…` and
  `container_runtime.py` `md5 4419d37c…` match the committed tree exactly.
- Redeployed the whole stack on the single tag. **13 harness containers now run
  `vs1coherent`**; no mixed application tags remain. Old tags
  (`vw22fix2/3/4`, `vw22orphan1`) are retained for rollback.

| Image (vs1coherent) | sha256 |
|---|---|
| harness-backend | `eb4b8a0a7d69c3f0…` |
| harness-engine-worker | `a8a2ab29adbaed1c…` |
| harness-worker-daemon | `a428fc232f96ef58…` |
| harness-frontend | `529bdce5883c678f…` |

Contracts in the coherent build: `harness_contracts 1.0.0rc7` (the workflow
contract the lane landed). Live model `anthropic/claude-haiku-5.5`, DB
Postgres-18, `ENGINE_LIVE_MODE=live`.

## W22 recovery — re-measured on the coherent build

`scripts/e2e/w22_daemon_kill_drill.py` against `vs1coherent`:
**PASS 8/8** (`docs/briefs/evidence/w22/w22-dkill-13f5e87c7a20.json`).

- Child live while the daemon was dead; lifecycle truthfully reported
  `mutationPossible: true` with 2 containers on the run network, then
  `false` after the reap.
- **Orphan lived 17 s** after the daemon returned — `f1016e2` (sweep in the same
  cycle) holds on this revision. Spend for this session's drill: $0.002238 (ledger).

This is **one** coherent-revision pass, not a rate. The residual reliability-rate
work (repeat the gate; the prior `vw22orphan1` set showed intermittent FAILs at
37 s and 214 s) continues to belong to Stage 1 and its owner.

## What this does NOT resolve — and why (honest boundary)

The quoted residual bundled several things that engineering cannot close, and
pretending otherwise is exactly what this repo forbids ("never claim a check you
did not run"; "implementation does not silently invent those decisions"):

- **W22 recovery gate as a whole**: the durable-reaper fix is now committed and
  re-proven once here, but Stage 1's exit ("repeat where reliability is claimed;
  every supported backend/topology exercised") is not met — it needs a reliability
  *rate* and backend/topology coverage, which is ongoing Stage-1 work.
- **SLO / on-call / public status / real alert endpoint**: by the roadmap's own
  rule a **named human owner** must adopt the objectives, assign escalation, and
  pick the paging destination. The two prerequisite *measurements* it still lacks
  are concrete engineering next-steps, not owner decisions to invent:
  `slo-and-capacity.md` says the **eligible-request availability counter** must use
  its documented exclusions, and "**No production end-to-end freshness histogram is
  currently confirmed … add a bounded-lag histogram before claiming attainment.**"
  I did not half-build those under active concurrent edits; they are the scoped
  next slice with the rule already written down.
- **Security review, legal review, W20 release decision**: paid external parties
  and an authorized human sign-off; a work order for each already exists in
  [`blocked-item-workorders-2026-10-31.md`](blocked-item-workorders-2026-10-31.md).
- **Stages 3–10 (W23–W33)**: sequenced future work, not started; not "resolvable"
  by a cleanup — they are the roadmap ahead.

## Deployment consequence to note

The coherent build deploys the **real** login (the operator-local auto-sign-in
that removed auth is preserved in stash `STAGE0-CARVEOUT`, not in `5e165fd`). So
the running frontend now requires real authentication. That is the correct
baseline posture; a developer who wants the local shortcut restores the stash and
rebuilds the frontend — a deliberate local-only choice, never the shipped default.

Verdict: the mixed-image cleanup is **done**; the product verdict is still
**NOT READY**, and Stage 1 recovery + the human/owner gates remain ahead —
accurately, not as an oversight.

## Daemon-kill reliability rate on `vs1coherent` (2026-10-31, 04:58–05:14 PDT)

Eight consecutive runs of `scripts/e2e/w22_daemon_kill_drill.py` against the
unchanged `vs1coherent` stack (daemon image `sha256:a428fc232f96…`), run back
to back with no code or config change between them:

| Mode | Runs | Verdict | Orphan gone after daemon returned |
|---|---|---|---|
| reaper | 5 | **5/5 PASS** (8/8 checks each) | 17 s in every run |
| `--orchestrator-restart` | 3 | **3/3 PASS** (durable reap 37 s, no user stop) | 37 s in every run |

Evidence: `docs/briefs/evidence/w22/w22-dkill-{516152e8dca6, 63df20776368,
517943c1c74d, 2596b4ecff01, e2f40f79a2df, 4b3943defd58, 44d8459ce5d3,
0789f54efb2e}.json`. Spend: 8 `cost_ledger` rows, $0.005872 in the window.

With the earlier single pass that makes **9/9 on this revision**, against 5/8 on
the pre-fix `vw22orphan1` set. What this still does **not** cover: one fault
(daemon SIGKILL) on one topology (single host, Docker Desktop). The other W22
gates (partial write, replay, provider outage) are still at "passed once", and no
second backend or topology has been exercised, so the Stage 1 exit stays open.

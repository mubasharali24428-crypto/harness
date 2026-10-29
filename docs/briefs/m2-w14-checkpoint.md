# M2 W14 status — all slices shipped, two live legs owed (2026-10-29)

Supersedes the paused checkpoint. Plan, audit and gate table:
`docs/m2-w14-plan.md`. The repository is the source of truth — re-verify any
number before relying on it.

## Done

| Slice | State | Evidence |
|---|---|---|
| Corpus contract gate | shipped, in `make static-gates` | `--check`: 39 tasks / 6 held out / 28 implemented |
| Operator rate card + `/estimate-cost` fix | shipped (HEAD `6afebdc`) | S3 144 passed; live check in `docs/m2-w14-plan.md` |
| Opt-in model routing (S1 API + S4 resolver) | shipped, live-smoked | S1 1019 passed; S4 1867 passed; 9 routing tests |
| Comparison runner (`--compare-model`) | shipped, **no live run** | guards exercised with zero spend |
| Cost/latency gauges + dashboard | shipped, live series verified | S1 26 observability tests; PG-gated test 2 passed |
| Fresh held-out design set | fixture leg PASS, **live leg owed** | `docs/briefs/evidence/eval/w14-design-fixture-1793271851.json` |

Live checks on the rebuilt image (2026-10-29): identity `/ready` 200 and
`/teams/{id}/settings/model-routing` GET (defaults) → PUT
`{"routing":{enabled:true,...}}` (team) → typo 422 → enabled-with-no-models 422
→ DELETE 204 → GET defaults. Orchestrator `/metrics` carries all six new series
(125 cost, 347 spend, 6 cap, 25+25 latency samples at check time).

## Owed (not defects)

1. W14 live design leg — needs a vision provider key and spend authorization.
2. A real `--compare-model` run on two priced models (currently n=0).
3. Opening `harness-cost-latency.json` in a live Grafana (structurally checked
   only).

## Fixed during this slice

* Routing could abort a run on a partial plan object (`SimpleNamespace(goal=...)`
  in six Section 4 tests): classification now reads `steps` /
  `filesLikelyToChange` defensively. Regression test added.
* Spec drift: `identity.openapi.json` and both SDKs regenerated for the three
  routing routes; the TS orchestrator client also picked up the previously
  stale checkpoints/rewind surface.

## Commands

```sh
cd "/Users/user/untitled folder 2/harness"
make lint && make static-gates
make test-section1 && make test-section3 && make test-section4
python3 scripts/eval/run_eval.py --check
python3 scripts/e2e/w14_design_heldout_drill.py --fixture-only
```

## Known-open outside W14

Task #4 "Close W13 execution leg" stays open by design: the runner holds a lease
but does not execute the approved plan (see `docs/m2-w13-plan.md`).

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
| Comparison runner (`--compare-model`) | shipped **with live runs** | `comparison-1793275608.json` — both models FAIL, no winner |
| Cost/latency gauges + dashboard | shipped, live series + Grafana render verified | S1 26 observability tests; PG-gated test 2 passed; `w14-dashboard-grafana.png` |
| Fresh held-out design set | **fixture + live leg PASS** (attempt 4/4) | `w14-design-fixture-1793271851.json`, `w14-design-1793274853.json` |

Live checks on the rebuilt image (2026-10-29): identity `/ready` 200 and
`/teams/{id}/settings/model-routing` GET (defaults) → PUT
`{"routing":{enabled:true,...}}` (team) → typo 422 → enabled-with-no-models 422
→ DELETE 204 → GET defaults. Orchestrator `/metrics` carries all six new series
(125 cost, 347 spend, 6 cap, 25+25 latency samples at check time).

Live model leg: the design run needed the provider key and the OpenRouter vision
patch on the deployment (the worker daemon had neither after a recreate); runs
1–2 failed on that configuration, run 3 was a genuine model failure, run 4
passed at ratio 0.004583. The comparison needed two single-model deployments
because the patch declares one model per deployment.

## Owed (not defects)

1. Metering engine model calls: `cost_ledger` has no rows for engine spend, so
   the spend panels see router-metered spend only.
2. Four catalog ids are dead on OpenRouter (`deepseek/deepseek-coder`,
   `qwen/qwen-2.5-coder-32b`, `qwen/qwen-2.5-72b`, `mistral/codestral`).

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

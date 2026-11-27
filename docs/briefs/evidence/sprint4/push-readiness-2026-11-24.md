# Sprint 4 push-readiness pack — 2026-11-24

Branch `w28-s1-delegate`, **local only — not pushed**. Pre-flight `d93ee869`, baseline
`3eb09124`; head at this commit (Task 29). Ahead of origin: **75 commits** at writing time.

## Task commits (Sprint 4)

| Task | SHA | Outcome |
|---|---|---|
| 21 | `ef21cd76` | PASS — compose up + migrate 0076–0081 on `:5499`, health 200 |
| 22 | `3b71212e` | PASS — section1 flake hardened, 2 consecutive full greens |
| 23 | `49445e4f` | PASS — `pg-probe` trio on compose PG |
| 24 | `8a78c5dc` | PASS — live approval + settings e2e, 7/7 with mocked spec |
| 25 | `29b7a8d7` | PASS — multiplayer pilot e2e on fresh P1 stack |
| 26 | `65aa4717` | done — C-05 dependency status + C-07 reject note |
| 27 | `fad665f8` | done — collab viewed_detail signal + documented seam gap |
| 28 | `1d09d7f2` | PASS — compose journey 13/13 |
| 29 | this commit | docs-only runbook + this pack |

Execution was partly parallel: tasks 22, 26 and 27 were committed while the compose image rebuilt;
none of them changed compose config or migrations, so Task 21's verification still describes the
running stack.

## Gate table template (fill from the END report at closure)

| Gate | Result at Task 29 |
|---|---|
| `make static-gates` | green |
| `make check-ts-dist` | green |
| `make test-section1` | 1299 passed / 62 skipped (twice, Task 22) |
| `make test-section4` | 2445 passed / 60 skipped / 0 failed (Docker up → sandbox tests pass) |
| `make test-section8` | 108 passed |
| frontend vitest + tsc | 121 files / 1238 tests, tsc clean |
| `HARNESS_TEST_PG=1 make pg-probe` | green on compose `:5499` (3+1 / 1361 / migrate selftest) |
| playwright approval specs | 7 passed (1 live + 6 mocked) |
| multiplayer.e2e.ts | 1 passed on fresh P1 stack |
| `make e2e-compose` | 13 checks green |
| `make gate` | **GREEN (rc=0)** at Task 30 — byte-for-byte ci.yml PR gate |

## Uncommitted artifacts — exclude from any push

- 32 pre-existing `docs/briefs/evidence/ui/r2-*.png`.
- 3 regenerated `docs/briefs/evidence/sprint2/x*.png` (Sprint 3 Task 15 run).
- Sprint 4 generated screenshots (`docs/briefs/evidence/sprint4/live-*.png`,
  `docs/briefs/evidence/p1/multiplayer-complete.png`) and regenerated
  `p1/browser.junit.xml`, `p1/live-result.json`, `p3/pilot-metrics-synthetic-run.json`.
- Untracked `.openclaw/`, `docs/briefs/evidence/eval/code-search-default-*.json`,
  `docs/briefs/evidence/quality/ci-local/`.

No secret, image or generated artifact was committed in Sprint 4.

## Recommendation

**HOLD. Do not push without an explicit owner request.** The branch is ahead ~75 commits and now
carries verified live-stack evidence, but owner gates (10.11, P-D2, P-D7, payment, all DEFAULT §17
signatures) are still open and no gate makes them green. See `docs/runbooks/compose-migrate-0078-0081.md`
for the operator migration step on any deployed database.

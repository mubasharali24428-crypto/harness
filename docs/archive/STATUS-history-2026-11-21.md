# STATUS history — 2026-11-21 (earlier) entry

Moved verbatim out of `STATUS.md` on 2026-11-24 (Sprint 2 Task 20) to keep that file under the
X-05 200-line policy. Nothing below was edited.

## 2026-11-21 (earlier) — weeks 9–13 fleet scope shipped: compliance pack, denial-learning loop, CI race fix, ledgers complete

Engineering slices from the 90-day plan's Month-3 fleet rows, all offline, zero
spend, on `w28-s1-delegate`:

- **P-19 / I-03 compliance evidence pack**: new
  `section1/src/harness_identity/audit_pack.py` + `GET /api/v1/audit/pack` —
  one bounded document = manifest (ranges/counts/explicit truncation/retention
  statements) + hash-chain verdict + team audit trail + collab events. Same
  admin+step-up door as `/audit/export` (classified `"export"` in
  `contracts/policy_enforce.py`), pack generation itself audited, no-drift with
  `/audit/export` via the shared `audit_rows` reader, honest `performed: false`
  chain verdict on non-PG lanes. 5/5 pack tests; section1 suite **1269 passed,
  63 skipped**; identity spec now 148 paths, python+TS SDK and dist rebuilt,
  `spec_drift.py` and `make check-ts-dist` green; sample pack committed as
  `docs/briefs/evidence/90day/p19-sample-pack.json`.
- **P-23 / I-08 denial-learning loop**: `section4` `denial_learning.py` — a W27
  `denials_repeated` stop now carries a deterministic bounded SUGGESTED project
  rule (the denied paths → include them in `scopePaths`) in the durable
  `engine_failed` reason; the human saves it through the existing rules API;
  `repeat_denial_stops` is the metric the plan's "repeat-denial count → 0"
  acceptance needs. 6/6 tests; section4 suite **2398 passed, 60 skipped**.
- **P-25 stabilization**: root-cause fixed the CI section11 race (`2ddf03b`,
  lease release before terminal status, W19 suite 5× green, 60/60); week-09…13
  evidence ledgers now exist for every plan-week's fleet scope.
- **P-21 / I-05**: policy DRAFT only (`i05-approval-envelope-policy-draft.md`);
  enablement stays owner-gated (P-D6, default off); no gate code changed.
- **I-04 benchmark leaderboard** (the last scheduled fleet-buildable idea):
  `scripts/eval/bench_leaderboard.py` renders the public leaderboard as a
  presentation of `bench_stats.aggregate` (no second arithmetic); MIN_N/dry/
  unknown-cost honesty laws inherited; dry-only corpus correctly shows "No
  claimable rows yet". Both formats (markdown + self-contained static HTML —
  no JS, no external assets, escaped against hostile model names) render from
  the SAME computed sections, so they cannot disagree; `make bench-leaderboard`
  regenerates both; 15 tests (41/41 across the eval suite); committed at
  `docs/briefs/evidence/w7-bench/leaderboard.md` + `.html`. Week-08's
  verdict-bot wiring row corrected to Implemented (verdict_post.py existed
  since the W4 wave).

Owner-gated and untouched: vv90r5 redeploy (runbook ready), P-13 live benchmark
(spend caps preregistered), P-14/P-16/P-18/P-20/P-22 revenue rows, F-05
signature, Day-90 gate (P-24). With I-04 done, every fleet-buildable item in
the 90-day plan (W1–W13 + scheduled ideas I-01…I-05, I-09, I-10) now has a
dated evidence entry; what remains is owner lane (outreach, SOW signatures,
spend, deploys, revenue).

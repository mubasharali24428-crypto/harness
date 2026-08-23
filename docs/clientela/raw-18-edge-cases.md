# Sweep E — Edge Case Registry (attempt 4, compact)

Source: S4 transition map. Format: `[component] transition | edge-class → actual → expected → fix — STATUS`
Legend: **UNHANDLED** = confirmed from S4 sweep · **VERIFY** = suspected, needs code confirmation · **HANDLED** = mechanism present per map.

## 1. Run lifecycle (idle→planning→awaiting_plan_approval→executing⇄paused→awaiting_done_signal→reviewing→DONE/BLOCKED)

1. [run] idle→planning | double-invocation → two invocations plan same run_id, second overwrites first silently → single winner, loser no-ops → fix: claim run row pre-planning (conditional `UPDATE … WHERE state='idle'`, assert rowcount=1) — VERIFY
2. [run] *→any | after-terminal → any transition attempted on DONE run → all hard-rejected → fix: central `is_terminal()` gate in single transition helper — VERIFY
3. [run] stop→DONE | double-invocation → second stop on DONE run → idempotent no-op returning DONE → fix: stop reads state, returns current if already DONE — VERIFY
4. [run] executing⇄paused | concurrent actors → pause races resume; ping-pong, duplicated work → monotonic CAS, one wins → fix: version/stamp column, compare-and-set — VERIFY
5. [run] reviewing→passed→EXECUTING | routing bug → **UNHANDLED**: pass verdict flips state BACK into executing = zombie rerun after review passed → passed routes to DONE (or explicit post-review terminal) → fix: correct routing + invariant test "no transition reviewing→executing"
6. [run] reviewing→failed→BLOCKED→resume | unlimited retries → **UNHANDLED**: BLOCKED→resume has no cap → cap N resumes or require fresh plan approval per exit; audit every resume — UNHANDLED
7. [run] awaiting_done_signal | timeout waiting → no expiry; run rots if done-signal never arrives → deadline + sweeper → fix: per-state TTL job → timed_out/cancelled w/ audit — VERIFY
8. [run] done-signal | double-invocation → **UNHANDLED**: double-POST processed under any reader-state; duplicate side effects → exactly-once → fix: single-use nonce consumed in same tx as effect; replay-cache returns first response — UNHANDLED
9. [run] done-signal vs stop | concurrent actors → interleaving order arbitrary, outcome nondeterministic → deterministic total order → fix: per-run serialization (row lock / queue) — UNHANDLED
10. [run] done-signal | crash after consume before effect → signal lost forever → consume+effect atomic → fix: outbox-style single transaction — UNHANDLED
11. [run] planning/approval | restart mid-transition → half-written state+event pair → atomic pair → fix: state + event writes in one DB tx — VERIFY
12. [run] executing | restart mid-transition → **UNHANDLED**: boot leaves stuck claim + lost task; nothing resumes → boot reconciler: reap stale claims (heartbeat TTL), requeue tasks — UNHANDLED
13. [run] executing→paused | DB-failure mid-write → caller sees error but state may/may not flipped → idempotent retry → fix: transition keyed (run_id, from_state, request_id) — VERIFY
14. [run] any | DB-failure mid-write of event log → state advanced w/o event (silent drift) → same-tx coupling → fix: forbid raw state UPDATEs outside helper — VERIFY
15. [runs dict] cleanup-on-exit → **UNHANDLED**: in-proc dict never evicts; leak grows per run → evict once DONE persisted → fix: LRU/TTL eviction keyed on terminal state — UNHANDLED
16. [runs dict] restart | in-memory truth → all non-persisted runs vanish → DB-first, memory as cache → fix: persist run rows at creation — UNHANDLED
17. [pause] timeout waiting to resume → indefinite paused if no human → paused-TTL → auto-cancel w/ audit — VERIFY
18. [reviewer] reviewing | double-invocation → two verdicts, last-write-wins → one claim, one verdict → fix: claim review slot like tasks — VERIFY
19. [reviewer] reviewing | timeout waiting → run stuck in reviewing forever → deadline → auto-BLOCKED w/ audit — VERIFY
20. [reviewer] verdict vs state write | DB-failure mid-write → verdict saved, state stale (or inverse) → atomic → fix: single tx for verdict+state — VERIFY

## 2. Plan approval (awaiting_plan_approval)

21. [plan] approve | double-invocation → double-click applies twice / errors → idempotent → fix: dedupe key (run_id, action) — VERIFY
22. [plan] approve | concurrent actors (approve vs reject race) → both applied → one wins on `state='draft'` rowcount → fix: conditional UPDATE — VERIFY
23. [plan] reject_plan | audit → **UNHANDLED**: no event/no audit row; restart evaporates rejection → event+audit in same tx as state change — UNHANDLED
24. [plan] approve | after-terminal / stale client → late approve mutates already-moved run → rejected unless state matches → fix: `WHERE state='awaiting_plan_approval'` — VERIFY
25. [plan] awaiting_plan_approval | timeout waiting → approvals rot indefinitely → TTL sweeper → expired/cancelled w/ audit — VERIFY

## 3. Claims (claimed→heartbeat→released|reclaimed)

26. [claims] heartbeat stops | worker death → claim held until TTL → reclaim picks up → **HANDLED** (mechanism: heartbeat→reclaimed) — confirm TTL value + sweeper exist — VERIFY(sweeper)
27. [claims] release | double-invocation → second release errors or clobbers → idempotent no-op → fix: delete-by-owner-token, rowcount-checked — VERIFY
28. [claims] release | concurrent actors → stale worker releases NEW owner's reclaimed claim → live claim destroyed → fix: owner/fencing token required on release — UNHANDLED(suspect)
29. [claims] reclaim | concurrent actors → old worker still alive during reclaim → split-brain dual execution → fencing token checked before every side-effect — UNHANDLED(suspect)
30. [claims] finish_run | cleanup-on-exit → **UNHANDLED**: deletes ANY claimant's row, killing another worker's live lease → delete only own token; foreign claim → requeue task instead — UNHANDLED
31. [claims] restart | persistence → if claims in-memory: boot orphans claimed tasks (pairs w/ #12) → persist claims; boot-time reaper releases expired — UNHANDLED
32. [claims] heartbeat | DB-failure mid-write → spurious expiry despite live worker → retry w/in TTL margin → fix: backoff retries, TTL ≥ 3× beat interval — VERIFY

## 4. Invites (draft→sent→redeemed|expired|revoked)

33. [invite] redeem | double-invocation → second redeem must fail → single-use enforced atomically → fix: `UPDATE … SET redeemed WHERE state='sent'` + rowcount check (not SELECT-then-UPDATE) — VERIFY(TOCTOU)
34. [invite] redeem vs revoke | concurrent actors → race decides membership → conditional UPDATE serializes on row lock → VERIFY
35. [invite] redeem vs expire | concurrent actors → expiry sweeper deletes row mid-redeem → redeem-first-then-validate inside tx; or soft-expire field checked in same UPDATE — VERIFY
36. [invite] send | double-invocation → duplicate invite emails → dedupe key (invite_id,'send') — VERIFY
37. [invite] revoke | after-terminal (already redeemed) → revoke silently succeeds post-redeem → reject/no-op → fix: `WHERE state NOT IN ('redeemed')` — VERIFY
38. [invite] send | DB-failure mid-write → email sent but state≠sent → resend dup later → route email through outbox (§6) — VERIFY
39. [invites] cleanup-on-exit → expired rows accumulate forever → TTL sweep/indexed expiry job — VERIFY

## 5. Keys (active→revoked, idempotent) & authz

40. [key] revoke | double-invocation → **HANDLED** (map: idempotent active→revoked; repeat revoke returns success).
41. [key] use vs revoke | concurrent actors → in-flight request authenticates just before revoke lands → brief window accepted → document + check revocation at request start; keep cache TTL ≤ 60s — VERIFY(window documented)
42. [key] revoke | DB-failure mid-write → partial revocation across tables → single-row atomic UPDATE only; no multi-table write sans tx — VERIFY

## 6. Outbox (pending→sent|failed)

43. [outbox] dispatch | crash after send, before mark-sent → resend duplicates downstream → consumer idempotency keys OR mark-then-send + reconcile scan — VERIFY
44. [outbox] dispatch | concurrent actors (2 workers) → both grab same pending row → duplicate send → fix: `FOR UPDATE SKIP LOCKED` or claim column — VERIFY
45. [outbox] failed→retry | timeout/backoff → immediate infinite retry storm → exp backoff, max attempts → dead-letter w/ alert — VERIFY
46. [outbox] shutdown | cleanup-on-exit → pending rows survive (persisted table) → **HANDLED** iff boot scanner re-drives pending — VERIFY(boot scan)
47. [outbox] mark-failed | DB-failure mid-write → row stuck pending forever → visibility timeout re-drive — VERIFY

## 7. Tickets / oauth_state (single-use + expiry)

48. [ticket] consume | double-invocation → second consume rejected → **HANDLED** (map: single-use) — confirm atomic claim (DELETE…RETURNING / conditional UPDATE), not TOCTOU — VERIFY(atomicity)
49. [ticket] consume vs expiry | concurrent actors → sweeper deletes row mid-validation → consume first, validate inside same tx — VERIFY
50. [ticket] expiry | cleanup-on-exit → unconsumed rows grow unbounded → TTL sweeper + reject-on-read by timestamp fallback — VERIFY
51. [oauth_state] abandon | timeout waiting → state rows never consumed on user drop-off → same TTL sweep as #50 — VERIFY
52. [oauth_state] callback | double-fire (browser retry) → second consume errors though login succeeded → user-facing error → keep consumed states briefly; return cached success — VERIFY

## 8. Meeting (open→closed)

53. [meeting] close | double-invocation → second close errors/no-op inconsistent → conditional UPDATE, idempotent — VERIFY
54. [meeting] ingest | after-terminal → events posted post-close rejected or parked → explicit guard + parked-queue policy — VERIFY
55. [meeting] close vs summarize | concurrent actors → race corrupts final summary → close takes lock; summarizer waits/aborts — VERIFY

## 9. Brief (draft→approved|rejected)

56. [brief] approve vs reject | concurrent actors → both land → conditional `WHERE state='draft'`; rowcount decides single winner — VERIFY
57. [brief] edit | after-terminal → draft mutated post-approval (approval no longer matches content) → freeze/version-copy content at approval — VERIFY
58. [brief] reject | audit parity → likely same gap as #23 (no event) → require reason, emit event same-tx — VERIFY

## 10. Cross-cutting

59. [transitions] global | every state change must emit event+audit atomically → today ad-hoc (#23 confirmed) → single `transition(entity, from, to, event)` helper; lint/ban raw UPDATEs — UNHANDLED(partially)
60. [restart] boot | no reconciliation scan → stuck claims (#12/#31), half-transitions (#11) survive reboot → boot reconciler: enumerate non-terminal runs/claims/tickets/outbox → drive safe — UNHANDLED
61. [timeouts] global | most waiting states lack deadlines (#7,#17,#19,#25,#51) → per-state TTL config + sweeper → timed_out w/ audit — UNHANDLED(mostly)
62. [side-effects] | SIGKILL between DB commit and external call (email/webhook/send) → effect lost or dup → all external effects via outbox §6; never inline in request path — VERIFY(scope)
63. [rowcount] | all conditional updates must assert rowcount==1 and branch on it → silent no-op transitions today → shared helper returns affected count; tests assert conflict paths — VERIFY
64. [terminals] | DONE-only-terminal invariant needs one enforcement point (#2,#24,#37,#53) → gate fn consulted everywhere — VERIFY
65. [registry hygiene] | this file mixes confirmed vs suspected → dedupe pass merges into registry w/ evidence links per line before triage — PROCESS NOTE

---
Tally: 65 items — 7 confirmed UNHANDLED (5,6,8,12,15,23,30) + 3 suspected-critical (28,29,60) · rest VERIFY/HANDLED. Priority order for fixes: 5 (zombie), 8 (done-signal exactly-once), 12+31 (restart recovery), 30 (finish_run lease kill), 6 (resume cap), 15 (dict eviction), 23 (audit).

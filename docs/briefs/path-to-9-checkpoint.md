# Checkpoint — Session 7: integrations, administration and billing (2026-10-19)

1. **Objective:** parts 49, 51 and 57. Parts 49 and 51 remain partial for provider/extension and
   high-assurance governance gaps. Part 57 remains partial and capped at 8 pending live provider evidence.
2. **Evidence/docs:** `docs/briefs/evidence/s7/{part49,part51,part57,session7-summary}.md`;
   `docs/{integrations,billing-and-quotas,organizations,permissions}.md`.
3. **Changed:** migration 0051; versioned team-scoped outbound webhook API/outbox/worker and Compose
   service; Section 7 integrations/governance pages; identity OpenAPI and generated Python/TypeScript
   SDK updates; capability matrix rows 49, 51, 57.
4. **Verification:** Section 1 targeted 58 passed; Section 4 budget/tier 45 passed; billing UI 7
   passed; Section 7 typecheck and TS SDK typecheck passed; touched Section 1 Ruff passed. Contracts
   Ruff reports two findings in untouched files (`__init__.py`, `ratelimit.py`). Exact commands and
   limits are in `evidence/s7/session7-summary.md`.
5. **Postgres/provider:** No PG migration or live Stripe/OIDC/provider delivery was run. Prior
   checkpoint records the unavailable Docker socket and local Postgres port; this session does not
   claim those checks passed.
6. **Open limits:** GitHub OAuth still requests broad `repo`; no issue-tracker/chat adapter or
   extension sandbox; governance has no dual policy approval, org-wide execution kill switch, or
   retention/residency controls; run count is not atomically reserved and tool-call/in-flight spend
   caps and alerts are absent. Part 44 stays partial.
7. **Next:** Session 8 prompt is `docs/briefs/path-to-9-session8-prompt.md` (parts 52, 58, and 31
   enforcement). Do not treat this checkpoint as permission to start it.

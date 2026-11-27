# Task 28 — Compose journey / X-03 regression

- Date (UTC): 2026-11-24
- Status: **PASS**
- SHA at verification: `29b7a8d7` (compose stack from Task 21 still up)

## Command / output

```sh
cd /Users/user/untitled folder 2/harness
export HARNESS_MASTER_KEY=<valid 64-hex key from the local env, never committed>
make e2e-compose
```

```
python3 scripts/e2e/compose_journey.py
compose E2E journey against http://127.0.0.1:8001 (+orchestrator/sandbox/frontend)
[ok] step 01 all 21 containers running/healthy
[ok] step 02 stack ready: identity+orchestrator+sandbox /ready, frontend 200
[ok] step 03 passwordless login A → solo team ... (admin)
[ok] step 04 demo seed → project/run, replay=true, pr_opened
[ok] step 05 demo re-seed idempotent (same project/run)
[ok] step 06 run aggregate → pr_opened with 10 steps
[ok] step 07 engine result truthfully reports 'replayed' (no live claim)
[ok] step 08 event replay → 10 marked events, 9 types, ordered
[ok] step 09 audit page → team-scoped rows incl. invite.minted
[ok] step 10 sandbox create→exec(passed)→destroy→post-destroy 404
[ok] step 11 cross-tenant reads answer identical 404 NOT_FOUND (no oracle)
[ok] step 12 metrics → 22 harness_* families
[ok] step 13 frontend serves HTML
PASS: 13 checks green in 3.0s
```

The journey auto-detected the locally published frontend port (`:3010`) from compose — no script
or env-doc fix was needed. No product regression was found, so no friction fix was made.

Full log: `/tmp/s4_e2e_compose.log` (runtime artifact).

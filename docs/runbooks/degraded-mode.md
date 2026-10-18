# RUNBOOK — Degraded-mode postures (provider outage & capacity loss)

> Doctrine ref: `docs/doctrine/mission-command-doctrine.md` §8.
> Companion: `docs/runbooks/ops.md`, backup-and-pitr.md. Process only — no code changes.
> Amendment rule: after every real invocation, append the posture timeline to §6.

## 1. Purpose

Provider outages are our best-documented operational risk: free-tier stealth models
hit daily OpenRouter 429s mid-mission, and the engine's fallback ladder honestly
BLOCKs runs when fallbacks exhaust (the anti-fake-success D2 guard working as
designed). Without a playbook that honesty becomes unplanned downtime plus
improvisation under stress. This runbook converts outage events into pre-planned,
rehearsed posture changes.

## 2. Triggers (numeric, so posture is never a mood call)

| Posture | Trigger | Who declares |
|---|---|---|
| GREEN | normal | — |
| AMBER | ≥ 2 timeout/429 events across distinct lanes within 30 min | ATLAS |
| RED | fallback ladder exhausted on ≥ 1 lane AND AMBER switch did not restore progress within 30 min | ATLAS |
| DRILL | scheduled once per mission series | ATLAS |

Detection ties to CCIR C2 (doctrine §10): any agent crossing a threshold escalates
immediately; ATLAS declares posture in the wave thread.

## 3. Postures

### AMBER — degraded but moving
1. Stop burning retries on the primary provider.
2. Switch remaining lanes to the proven paid-model recipe (mission10-lessons):

       export OPENROUTER_KEY=$(grep -E '^OPENROUTER_API_KEY=' ~/.hermes/.env | cut -d= -f2)
       DEEPSEEK_BASE_URL=https://openrouter.ai/api/v1 \
       DEEPSEEK_API_KEY="$OPENROUTER_KEY" DSH_MODEL=qwen/qwen3-coder \
         docker compose -f deploy/docker-compose.yml up -d orchestrator

   (Redeploy frontend images only if the wave already merged something.)
3. Cap concurrent lanes at half-width; longest-running lanes first get priority.
4. ATLAS announces AMBER + reason in the wave thread; agents keep working.

### RED — freeze fan-out
1. No new dispatches. Wave converts to single-lane critical path:
   open fix-loops, RED-squad blockers, and merge-critical wiring ONLY.
2. Agents checkpoint state: write uncommitted work + a 5-line state note
   (what's done / what's next / what's blocked) into their lane file, then idle
   cleanly. This mirrors the honest-BLOCK guard: stop cleanly, don't thrash.
3. Checkpointing protects lane work against ATLAS traffic stash/pop cycles —
   untracked NEW files survive lane traffic; tracked-file edits may be reverted
   mid-flight, hence write early, write often.

### DRILL — rehearse the switch
Once per mission series, run one throwaway lane end-to-end in AMBER posture on
purpose (point it at a deliberately rate-limited endpoint). Success = posture
declared within 10 min of trigger, recipe commands executed verbatim, zero lost
lane work. Log results in §7.

## 4. Exit criteria

- AMBER → GREEN: primary provider healthy across 3 consecutive probes ≥ 10 min apart.
- RED → AMBER: fallback restored progress on the critical path.
- Any exit: ATLAS announces in wave thread; agents resume normal fan-out.

## 5. Invariants during any degraded posture

- No auto-merge. Human-gated merges remain human-gated (AGENTS.md law).
- No forced model downgrade to an untested model just because it responds.
- Gates do not loosen: footprint → rerun → live-probe still applies per delivery.
- Honest reporting unchanged: BLOCK beats fake success, always.

## 6. Invocation timeline (appended after every real use)

| Date | Trigger seen | Posture | Time trigger→declaration | Outcome |
|---|---|---|---|---|

## 7. Drill log

| Date | Lane | Result | Notes |
|---|---|---|---|

## 8. Platform dependency failures (not provider outages)

Sections 1-7 cover LLM-provider outages during build missions. For runtime
dependency loss in the deployed stack, see
[ops.md](ops.md) section 3.2-3.3. Summary of the degraded states the product
itself exhibits:

| Lost | Behaviour | Operator action |
|---|---|---|
| Postgres | backends `/ready` -> 503 `db-unreachable`, containers keep running (no restart storm), `/health` stays 200; in-flight runs fail their next DB write | restore db; backends self-recover on the next probe. If data loss: [backup-and-pitr.md](backup-and-pitr.md) |
| Orchestrator worker | `/ready` stays 200; runs sit `QUEUED`; `harness_oldest_queued_run_age_seconds` grows | `docker compose restart orchestrator` |
| Model provider | engine fails runs to `blocked` (never fake success); breaker opens after 3 timeouts | section 3 postures; [engine-concurrency.md](../engine-concurrency.md) |
| Redis / broker | n/a: none in the stack | - |

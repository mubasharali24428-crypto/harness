# Mission 11 Graph — ALEX-250: 5 Teams × 5 Fire Teams × 10 Agents

Base: harness repo @ mission-10 state. Runtime change shipped first:
`delegation.max_concurrent_children = 70` (was 30, before that 10) — verified via
`hermes config get`.

## Topology

```
ATLAS-PRIME (ox-alpha session, depth-1 spawner)
│  1 PM persona · owns graph, contracts, merge gates, single commit law
│
├── TEAM ALPHA   ── FT-A1..A5 (5 fire teams × 10 agents)
├── TEAM BRAVO   ── FT-B1..B5
├── TEAM CHARLIE ── FT-C1..C5
├── TEAM DELTA   ── FT-D1..D5
├── TEAM ECHO    ── FT-E1..E5
                    = 25 fire teams · 250 builder agents total
```

## Runtime laws (non-negotiable)

1. **Depth-1 spawn:** Hermes nesting max is 1. Fire teams and teams are
   SCHEDULING DOMAINS in the dispatch ledger, not parent agents. Every one of
   the 250 builders is a direct leaf child of ATLAS-Prime.
2. **Concurrency 70:** at most 70 children run simultaneously — two full fire
   teams plus headroom. The scheduler rotates the 25 fire teams through the 70
   slots; teams interleave rather than run wholesale.
3. **Disjoint file ownership:** every agent gets a lane table row; any two
   agents sharing a file = graph bug = stop-the-line.
4. **Verify-as-you-go:** each completed lane triggers its verifier pair
   immediately (user-mandated pipelining); no batch-and-pray merges.

## Dispatch ledger schema (the management instrument)

`docs/briefs/mission11-ledger.csv` — one row per agent:

| col | meaning |
|---|---|
| agent_id | ALPHA-A1-01 … ECHO-E5-10 (team-fireteam-slot) |
| team / fire_team | scheduling domain + rotation order |
| task_brief | pointer to brief section below |
| owned_paths | verbatim; overlap check runs on write |
| status | queued → dispatched → building → verifying → merged \| fix-loop \| dead(503) |
| verify_pair | which pair audits it and when dispatched |
| evidence | transcript path + pasted-output anchor |

## Rotation algorithm (how 30 slots serve 250 agents)

```
while queue nonempty:
    ready = next fire team(s) with all dependencies met (layer order)
    dispatch up to (70 - running_count) builders, mixing fire teams to fill slots
    on each completion: trigger verifier pair for that lane
    on pair READY verdict: ATLAS-Prime merges lane into staging branch
    dead(503)/budget-death → footprint audit → re-dispatch identical brief
```

Layers gate across fire teams: Layer N+1 waits only on the *contracts* of
Layer N, not whole-team completion.

## Mission assignment (first deployment)

Target: NetMax desktop app (`/Users/user/netmax-app`) Layer 4 productization +
Harness Mission 10 completion support, split as:

| Team | Domain | Sample lanes |
|---|---|---|
| ALPHA | NetMax Swift UI | scheduled tests, notifications, report-card PDF |
| BRAVO | NetMax engine/Python | SQLite migration, watch daemonization, export API |
| CHARLIE | Harness M10 backlog | A4 runs-api, B3 onboarding, B4 walg-pitr, B5 rate-limit |
| DELTA | QA + verifier pairs | adversarial probes, seam tests, perf baselines |
| ECHO | Docs/release | README, DMG pipeline, notarization checklist, website copy |

## Merge law

Single mission commits by ATLAS-Prime per layer; verifier verdict table must be
all-READY before sweep; rollback = revert layer commit.

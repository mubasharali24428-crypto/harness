# COMMAND BRIEF TEMPLATE (Auftragstaktik — v0.1)

> Copy this whole file into `docs/briefs/wave<N>/<lane>-brief.md` and fill it.
> Rule: the four blocks below are ALL a brief may contain. Method stays free.
> The INTENT line is the first line after the title, always.

# <LANE-NAME> BRIEF (wave N)

INTENT — <so that <operational reason>>, deliver <artifact> such that
<observable done condition>. ≤ 40 words. Stays true even if sibling lanes fail.

## END-STATE

<Observable conditions for DONE. Verifiable from artifacts: tests, probes,
rendered pages. No narrative, no method. Example: "test_api_keys_routes.py
passes 9/9 in section1 venv; mint→list→revoke walk succeeds against live app;
full key never re-appears in any response.">

## CONSTRAINTS

<Frozen things + hard edges. If it isn't written here, it doesn't exist.
Example: contracts/ FROZEN; conftest.py FROZEN; migration number = next free
slot (re-check immediately before naming); siblings' dirty files expected —
never revert them; no new dependencies; no section imports outside
harness_contracts.>

## OWNERSHIP

<Exact paths this agent may create/modify — one owner per file. Everything
not listed is read-only.>

## BACKBRIEF  ← filled by the AGENT before first write

- Intent in my own words: <one sentence>
- Paths I will write: <list>
- Frozen things I will NOT touch: <list>
- My done-proof will be: <tests/probes I will run to demonstrate END-STATE>

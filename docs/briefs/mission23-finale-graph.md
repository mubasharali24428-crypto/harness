# MISSION 23 — BETS + FINAL POLISH — TASK GRAPH (PROGRAM FINALE)

> ALEX-ELITE dual teams. Baseline: 8bf1e61. Source: mission17-10x-plan.md
> §Bet horizon + program-wide final polish. This is the LAST mission; on
> close the 10x program completes at 100%.

| Lane | Mission | Owned paths | Team |
|---|---|---|---|
| Z1 | Program AAR + scoreboard: every mission's verdicts audited (ATLAS calibration pass), evolution/ledgers/ rows for all lanes, campaign growth chart regenerated to final state, STATUS.md program-close section | docs/briefs/m23-aar.md (NEW) · evolution/ledgers/ · scripts/campaign_growth_chart.py · STATUS.md | 2 |
| Z2 | Bet lane A — decision-gated per plan: evaluate the two candidate bets (AST-aware chunking upgrade for context pillar; multi-replica live proof of stateless orchestrator) and EXECUTE whichever has cheaper proof cost; document the other as Next-owner handoff | section11/src/harness_context/chunking.py OR deploy compose replica proof · tests | 1 |
| Z3 | Final polish sweep: repo-wide dead-code/duplication scan (vulture-style), TODO/FIXME inventory triaged, docs link-check all .md, CHANGELOG.md generated from mission history | CHANGELOG.md (NEW) · touched files from scan · docs/ | 2 |
| Z4 | Release readiness: version stamps aligned repo-wide (contracts + all sections + SDKs), git tag v1.0.0-candidate, release notes draft, deployment checklist verified against rollback runbook | pyproject/version files · RELEASE_NOTES_v1.md (NEW) · tag (commander applies) | 1 |

Gate law: same as all waves. Exit criteria: AAR published with calibrated
verdicts; one bet executed with evidence; zero dead links; CHANGELOG covers
missions 11–23; v1.0.0-candidate tag ready; suites S1/S3/S4/S11/mcp/S7
all green at HEAD.

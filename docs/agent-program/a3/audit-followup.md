# A3 audit follow-up (A2 agent re-check, uncommitted)

Re-ran every A3 suite: section5, verification, browser worker, desktop — all reproduced the reported counts.

## Defects found and fixed (each has a regression test)
| Defect | Fix | Test |
|---|---|---|
| Snapshot restore wrote outside the workspace when a snapshotted dir was swapped for a symlink (leaf missing, so the old guard was skipped) | parent must resolve inside workspace | `test_restore_never_writes_through_a_swapped_symlink_dir` |
| `BrokeredFetcher` validated DNS, then urllib re-resolved (rebinding TOCTOU) | default transport connects to the validated IP; Host/SNI keep the name | `test_fetch_connects_to_the_validated_ip_not_a_second_dns_answer` |
| Redirect `Location` matched case-sensitively | case-insensitive | `test_redirect_location_header_is_case_insensitive` |
| Network-index installs allowed without a hash; user pip.conf / PIP_* env could add indexes | hash required for `index:` sources; `PIP_CONFIG_FILE=/dev/null`, inherited PIP_* dropped | `test_network_index_install_requires_a_hash...`, `test_pip_runs_without_user_config...` |
| Browser state.json (cookies) plaintext at rest | AES-256-GCM `state.enc`, fail closed without key | `sealed state and durable replay` (vitest) |
| Replay log in memory only | `replay.jsonl`, reloaded on reopen | same suite |
| Stray `diagnostics.ts.bak` in src | removed | — |

## Results after fixes
section5 235 passed/1 skipped; verification 23; browser worker 75; desktop 9; ruff and tsc clean.

## Still open (needs a peer lane or external input — unchanged)
Docker-backed runs (daemon down); real macOS input smoke; SDK→S5→browser live wiring (A2/A5); Postgres lease/consent tables (A1 migration); approval/takeover UI (A4); stale `section5/openapi.json` (A5); consent grants, quota, network broker, worker pool and environments have no HTTP routes yet; cross-team preview port isolation on shared loopback; snapshot quota can block the restore safety-snapshot; JS test execution (F041), model-backed generator/fixer (F042/F043).

# Whole-project test run — 2026-10-26

Every Python section suite, the MCP server and the frontend, run in full rather
than stopped at the first failure (`make test` exits on the first bad section,
which hides everything after it).

| Suite | Result | Notes |
|---|---|---|
| section1 | 992 passed, 54 skipped | one ordering flake in a separate run, below |
| section2 | 48 passed | |
| section3 | 128 passed | |
| section4 | 1722 passed, 58 skipped | |
| section5 | 319 passed, 8 skipped | |
| section6 | 123 passed | |
| section8 | 91 passed | |
| section10 | 4 failed → 91 passed | host `NO_PROXY`, see below |
| section11 | 1 failed → 50 passed | same cause |
| mcp | 3 failed + 15 errors → 18 passed | same cause |
| section7 (frontend) | 1003 passed, tsc clean, `next build` green | run earlier today |
| static gates | green | routes, isolation over 245 files, TS mirror, spec drift |

## What the run actually found

**1. A deployment-shape defect, not a test defect: Docker Desktop's `NO_PROXY`
kills httpx clients in three more services.** With the host's
`NO_PROXY=[..., [::1]]` present, section10 (4 tests), section11 (1) and the MCP
server (3 + 15 errors) all raise `httpx.InvalidURL: Invalid port: ':1]'` at
client construction; with the variable unset all three are green
(91/50/18 passed). This is the same root cause fixed in Section 1's SSO path
today (`env_client`), and it is NOT only a test artifact: any of those services
running in an environment whose `NO_PROXY` carries a bracketed IPv6 literal fails
the same way at runtime.

The fix belongs in `harness_contracts` — sections may not import each other, so
copying the helper into three services multiplies the drift risk the isolation
guard exists to prevent. A contracts addition means a version bump plus generated
mirrors and golden conformance in the same change, which is a deliberate decision
rather than a quick patch, so it is recorded in `docs/backlog.md` and not done
here.

**2. A regression I introduced and the guard that caught it.** Section 1's
deploy law failed on `services without resource limits: ['engine-worker']` —
the build-only compose service M07 added had no caps and no log rotation.
`make static-gates` does not check this; only Section 1's suite does, and I had
not re-run Section 1 since adding the service. The caps are now in the working
tree (applied there by the parallel writer on this branch, not by me — my own
edit did not land, so their version stands).

**3. One ordering flake, in the opposite direction from the last two.** With
`NO_PROXY` unset, section1 failed
`test_ws_replay_cap.py::test_resume_never_resends_at_or_below_afterseq_during_concurrent_publish`,
which passes 5/5 in isolation and passed in the run where `NO_PROXY` was set. So
it is load/order dependent — the same class as the two Section 4 flakes fixed
today, and it sits exactly in the reconnect/replay-dedup area W1 recorded as
unverified end to end. Not root-caused here.

## Reading this table honestly

Green sections are green on this machine with this host env and this local model.
"Passes with `NO_PROXY` unset" is a statement about the test environment, not a
clean bill of health for a deployment — which is precisely the point of finding
1.

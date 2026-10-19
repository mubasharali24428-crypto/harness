# Real OTP sign-in, two-user browser proof (parts 25, 26) — 2026-10-18

Command: `section4/.venv/bin/python deploy/pilot/run_smoke.py --otp > docs/briefs/evidence/s1/logs/run_smoke_otp.log`
Result: `1 passed (23.7s)`, retries=0, zero console/network/page faults (`multiplayer.e2e.ts`).
Change: new `--otp` mode in `deploy/pilot/run_smoke.py` sets `HARNESS_REQUIRE_OWNERSHIP=true` and `E2E_OTP_SIGNIN=1`;
`section7/e2e/multiplayer.e2e.ts` then drives the real login form -> OTP step -> reads the code from the identity service log
(dev notifier; no SMTP) -> "Verify code". The only exempted fault is the expected first 403 on `/auth/login` that flips the form to OTP.
Covered in the browser: create run, join, leader claim, peer guidance seen by leader, handoff offer/accept, withheld deletion shown,
approve, apply, Done unblocks, stop, summary refresh.
Not covered by this browser run: changed-proposal re-approval, cross-run isolation, reconnect/refresh survival (backend tests only; see
`verification.md`). Worker is the deterministic stand-in (`deploy/pilot/worker.py`); the real runtime needs `--real` + a free OpenRouter key (not run).
Delivery of the code by email is NOT proven (log capture only). Port-bind check in `run_smoke.py` fails for ~1-2 minutes after a prior run (TIME_WAIT).

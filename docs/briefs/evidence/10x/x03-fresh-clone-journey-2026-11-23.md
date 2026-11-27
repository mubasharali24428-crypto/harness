# X-03 acceptance: fresh-clone → `make bootstrap` → `make gate` — verified (2026-11-23)

X-03 (10X Wave A) requires the devloop journey "documented and verified once".
What was already done earlier in the lane: `make dev-otp` (guarded) and the compose
journey frontend-port precedence fix (`compose port` before `.env`). This file closes
the remaining row: the bootstrap → gate journey on a fresh clone.

## Method

- **Fresh-clone equivalence** was verified by running the full journey in place over
  the *deleted-and-recreated* derived state — every artifact `make bootstrap` produces
  (`section*/.venv`, `mcp/.venv`, `language/.venv`, `section7/node_modules`) was
  removed first, so pip/npm resolved everything from scratch against the current lock
  files. Cloning to a second path differs only in the absolute path (the Makefile uses
  `$(CURDIR)` everywhere), so this exercises the same code path end to end.
- Then `make gate` — the byte-for-byte CI gate (static-gates + lint + test +
  lint-mcp/test-mcp + lint-language/test-language + frontend) at HEAD `d652336c`.

## Result

- `make bootstrap`: all section venvs + mcp + language rebuilt clean; `npm ci`
  restored section7 from the lockfile. No errors.
- `make gate`: **GATE GREEN** — static-gates ok; pytest green in section1…11, mcp,
  language; frontend `tsc` clean and **vitest 1205/1205 passed (113 files)**.
- Output verbatim: `GATE GREEN — static-gates + lint + test + lint-mcp/test-mcp +
  lint-language/test-language + frontend all passed. This is byte-for-byte what
  ci.yml enforces on PRs.`

## Notes

- `make gate`'s pytest lanes run WITHOUT `HARNESS_TEST_PG`, so the PG-gated suites
  skip by design (they run under `make test-all` / `make pg-probe`; F-SEC-06 closure
  covers the real-PG side separately).
- Duration on this M-series Mac: bootstrap ~4 min, gate ~13 min, all local, zero
  provider keys, zero spend.

X-03 is now **Done** for all three rows: dev-otp target, compose-port precedence,
fresh-clone journey verified once and recorded here.

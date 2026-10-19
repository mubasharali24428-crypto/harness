# Lane O evidence (part 37)

Files: `section1/migrations/0038_organizations.sql`, `section1/src/harness_identity/orgs.py`, `section1/tests/test_orgs.py`, `docs/organizations.md`.

Commands (cwd `section1`):
- `./.venv/bin/python -m pytest tests/test_orgs.py -q` -> 21 passed
- `./.venv/bin/python -m pytest -q -p no:warnings` -> 736 passed, 26 skipped (whole section1, includes other lanes' files present at run time)
- `./.venv/bin/ruff check src/harness_identity/orgs.py tests/test_orgs.py` -> All checks passed; `ruff format --check` clean
- `./.venv/bin/ruff check src tests` -> 36 errors, none in lane O files (other lanes' in-progress test files); `--fix` never used

Tests run the real migration SQL on SQLite (ORM create_all, then 0038 statements), router mounted on a scratch app.
Invariants covered: cross-org 404 on every route, escalation denied (member, admin to owner, admin touching owners, invite owner, SSO flag by admin), last-owner on demote/remove/leave/identity delete, invite expiry/revoke/redeem/identity binding, domain policy at mint and accept, expired and signed-out session 401, SSO/MFA flags with owner break-glass, history before/after, guest expiry (404 after, one audit row, no member list for guests), service identity (hash only, closed scopes, last_used, revoke, expiry, no org role), next-request-401 after member removal, team departure, org deletion, identity deletion.

Gaps: see "Known gaps" in `docs/organizations.md` (audit-referenced team seats, assurance hook not wired in sso.py/app.py, no MFA factor, provider api_keys not user-bound, Postgres not run, live SSO/Stripe gated, router not mounted in app.py).

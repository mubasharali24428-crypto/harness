# Part 49 — integrations and extensibility

## Directive map

| # | Result | Evidence / remaining limit |
|---|---|---|
| 001 | Partial | Versioned team-admin outbound webhook API in Section 1; no general scoped credential API for every provider. |
| 002 | Partial | GitHub OAuth defaults to `public_repo read:user`; private-repository access is an explicit deployment opt-in. Classic OAuth still grants write access to public repositories; no issue tracker adapter exists. |
| 003 | Partial | Outbound webhook destinations require explicit per-team setup; no chat provider integration. |
| 004 | Implemented locally | HMAC timestamped signatures, timestamp replay window on consumer side documented, SSRF checks, bounded retry worker; invariant tests cover signature and retry identity. |
| 005 | Partial | Stable delivery id/idempotency key for outbound deliveries; provider-specific external side-effect idempotency is not implemented uniformly. |
| 006 | Out of scope | No third-party extension execution surface exists, so no extension sandbox is claimed. |
| 007 | Partial | Fixed payload projection, endpoint host allowlist, DNS public-address check, no redirects/proxy env; egress firewall remains an operator control and DNS rebinding is residual risk. |
| 008 | Implemented for outbound webhooks | Status, pause/resume, revoke/delete, one-time secret and rotation; provider reconnect/rotation differs by provider and has no common health API. |
| 009 | Partial | `/v1` boundary and compatibility policy documented; no automated deprecation checker or published version schedule. |
| 010 | Partial | Existing approval/policy gates remain for internal tool actions; no uniform external-tool approval registry. |

## Verification

- `section1/tests/test_webhooks.py`: team/admin scoping, allowlist, encrypted one-time secret,
  expected-version conflict, pause/resume, rotation, deletion, identity-only payload, signature,
  retry and stable delivery key.
- Existing Stripe adapter verifies raw-body signature timestamp and deduplicates event id
  transactionally (`section1/src/harness_identity/stripe_adapter.py`).
- GitHub scope mode and fallback are verified from `section2/src/harness_github/app.py`; OIDC scope is
  `openid profile email` in `section1/src/harness_identity/sso.py`.
- No live provider test or external endpoint delivery was run. Part remains partial because the
  full least-privilege read-only GitHub access, issue tracker/chat adapters, and extension governance
  are not complete.

Operational setup and consumer verification instructions are in `docs/integrations.md`.

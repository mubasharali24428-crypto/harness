# A1-S1 — Outbound security and enforceable capabilities

Owner: A1 (Security, Identity, Models and Commercial Governance)
Session: A1-S1
Features: F091, F092, F093, F094, F095, F096
Risks: VR011, VR012, VR015, VR017, VR019

## What was built

New package `packages/security-policy/` (`harness-security-policy`, stdlib-only) implementing
six pure, independently testable evaluators:

- `secret_scan.scan_outbound` (F091): regex secret scanner with REDACT/DENY outcomes; denies critical
  (PEM keys, Stripe live keys), redacts the rest; never echoes matched material in findings.
- `injection.inspect` (F092): weighted heuristic scoring plus quarantine frame (`frame_untrusted`) that
  neutralizes embedded guillemets so content cannot break the data-only frame.
- `sensitive_files.evaluate_path`/`evaluate_transmission` (F093): path gate (deny globs, traversal
  rejection, exact allowlist) combined with content secret scan.
- `dlp.inspect_outbound` (F094): checksum-validated DLP (Luhn cards, mod-97 IBANs, SSN ranges,
  credentialed connection strings DENY); informational signals do not alter outcome.
- `capabilities.CapabilitySigner` (F095): HMAC-SHA256 signed, short-lived, team/run/operation/audience/
  argument-bound grants with `InMemoryReplayStore` use budgeting.
- `egress.evaluate_url`/`evaluate_redirect_chain` (F096): fail-closed egress policy with DNS resolution
  classification (SSRF/link-local/metadata denied) and per-hop redirect checks.

All decisions share one `Decision`/`Finding` vocabulary (`decisions.py`) that carries spans, reason
codes and redacted text but never matched secret material, so it is safe for audit detail or wire
transport.

## Audit corrections (re-verified, not carried over from the earlier report)

- The earlier "ruff clean / `.venv`" claim was false: no `.venv` existed and ruff reported 2 errors (import
  order, E501). Fixed. `hap.py` used the class attribute `RequiredBinding.args` as the "unset" sentinel by
  accident; now uses `UNSET` explicitly.
- DEFECT FIXED: `secret.private_key` required a PEM END marker, so a truncated/chunked
  `-----BEGIN ... PRIVATE KEY-----` was ALLOWED. END is now optional; header alone is DENY.
- `hap.py` (HAP/1 + simulator) had no tests; `tests/test_hap.py` added.
- The earlier "section1 adapter next session" gap is closed here: `section1/src/harness_identity/policy_api.py`
  adds `POST /api/v1/policy/evaluate` (HAP/1, bearer principal, project-ownership 404, dry capability verify,
  denials recorded as audit `policy.denied`, ids/reason only) and `POST /api/v1/policy/capabilities`
  (admin/engineer only, audit `policy.capability_issued`, token never stored/logged). Wired in `app.py`
  behind an optional import so a missing package disables the endpoints instead of the identity service.
  Audit allowlist extended in `audit.py`/`audit_emit.py` (no migration needed).

## Tests (run 2026-10-17)

- `packages/security-policy`: `PYTHONPATH=src python -m pytest` -> 77 passed; `ruff check src tests` clean
  (clean venv at /tmp/a1venv; the package has no committed venv).
- `section1`: `./.venv/bin/python -m pytest tests -q` -> 647 passed, 10 skipped (baseline 639/10 + 8 new in
  `tests/test_policy_api.py`); `ruff check src tests scripts` clean.
- New adapter tests: unauthenticated 401, secret-bearing prompt DENY with no secret in response or audit,
  redaction never echoes secret, foreign project 404, body teamId ignored / cross-team + wrong audience /
  wrong operation / wrong run capability denials, viewer cannot mint grants, grant audit holds no token,
  link-local egress denied, bad schemaVersion 422.

## Dispositions

| F | Status |
|---|---|
| F091 | VERIFIED IMPLEMENTED (pure engine + unit tests) — NEEDS INTEGRATION: wire as outbound gate in section1/section4 before model/tool transmission |
| F092 | VERIFIED IMPLEMENTED (heuristic; explicitly non-universal) |
| F093 | VERIFIED IMPLEMENTED (evaluator) — NEEDS INTEGRATION: section5 remains execution-time authority; this is the A1 decision point |
| F094 | VERIFIED IMPLEMENTED (evaluator) |
| F095 | PARTIAL: signer/verifier + issuance endpoint done; evaluate endpoint is DRY (does not consume uses). Durable cross-replica replay store needs migration 0035 (not yet written) -> NEEDS INTEGRATION |
| F096 | VERIFIED IMPLEMENTED (evaluator) — NEEDS INTEGRATION: section5/section4 must call per run |

## Risks

- VR011: PARTIAL CONTROL — quarantine frame reduces but cannot eliminate injection; paired with egress/tool policy.
- VR012: PARTIAL CONTROL — outbound secret scan added; raw secrets still never enter prompts by policy; needs wiring.
- VR015: PARTIAL CONTROL — capability grants enable fine-grained tool authorization once enforced at SDK boundary.
- VR017: PARTIAL CONTROL — grants and evaluator are team/project bound; cross-tenant tests in section1/tests/test_policy_api.py.
- VR019: OPEN — mock IdP drills still pending (not in this session).

## Integration proposal (non-owned)

Adapter now exists (above). A5 must add `harness-security-policy` to the section1 deploy image (otherwise
the endpoints are off). Not yet proven: that S4/S5/SDK callers consult it. A3 (section5) keeps execution-time authority; this package is the
decision point, not a bypass.

## Not done in S1 (honest gaps)

- Postgres-backed replay store for capability use budgets (needs next free migration; 0014 is a gap, latest is 0034).
- No SDK/S5 enforcement link; F091/F093/F096 are decision points only.
- Live provider/IdP drills (VR019) untouched.

# MISSION 15 — WAVE 5 (roadmap program, mission 1 of the steady state) — CAMPAIGN GRAPH

> ATLAS-authored. Baseline: Wave-4 close HEAD (CI SUCCESS, baf2299+). Per
> part4 §W5+: quarterly-scale program, ≤10 lanes per mission, prioritized by
> part2 cluster severity. This mission takes the highest-severity roadmap
> items; later missions drain the rest.

## W4 CARRYOVERS into W5

1. Placeholder-only accessible names on meeting chat input, review comment
   textarea, login inputs (VT-2 non-blocking) → A3 lane.
2. Login fixed-width cards >320px comfort (VT-2 note) → A3 lane.
3. NUMERIC(12,6) budget cap operator note in billing UI + .env.example → A1 lane.
4. Route-module law now enforced by convention — add a CI grep guard so it
   stays enforced → G6 lane.

## WAVE 5 LANES

| Lane | Mission | Owned paths | Squad |
|---|---|---|---|
| S1 | CSP enforce-mode rollout: Content-Security-Policy header report-only→enforce behind HARNESS_CSP_ENFORCE env, nonce-based script allowlist, report-uri to /csp-report collector | section1/src/harness_identity/security_headers.py · deploy compose env docs · tests | ALPHA |
| S2 | WS ticket client-binding (L3): bind ws tickets to issuing client (UA+IP-hash fingerprint), reject mismatched upgrades | section1 ws ticket mint path · section7 useEventStream send · tests both sides | ALPHA |
| S3 | Accessible names + login responsive fix: aria-labels on all placeholder-named inputs; login card max-width 320-comfortable | section7 app/meeting chat input · app/review textarea · app/login/page.tsx (+globals if needed) | BETA |
| S4 | Salted KDF upgrade (M4): master-key derivation → Argon2id w/ salted per-install params; re-encryption migration for stored Fernet payloads; dual-read during transition | section1/src/harness_identity/crypto.py · migration 0022_kdf_upgrade.sql · KeyVault tests | BETA |
| D1 | FK deletion story (DB#6): documented cascade map for every FK; ON DELETE behavior audited and made explicit; deletion-cascade test suite | section1/migrations/0023_fk_deletion_story.sql · models.py ondelete= sweep · tests | ALPHA |
| D2 | Vacuum/maintenance schedule (DB#12): autovacuum tuning for agent_events + rate_buckets, maintenance runbook, compose-side scheduled VACUUM ANALYZE job | deploy compose profile · docs/runbooks/maintenance.md · tests where feasible | BETA |
| G6a | CI route-module law guard: grep step failing on non-default exports from app/**/page.tsx + contracts-import guard for section isolation law | .github/workflows/ci.yml (make target) · Makefile · docs/ci-gates.md | ALPHA |
| G6b | Reviewer-heuristic tuning (raw-02 L4): reviewer pass/fail thresholds configurable per team settings; document heuristic; add calibration test corpus | section8 reviewer heuristic module · team settings storage · tests | BETA |
| G7 | Docs/compat debt batch (raw-12): README accuracy pass, API versioning doc, changelog discipline automation note, glossary completion | README.md · docs/api-versioning.md (NEW) · CHANGELOG.md · glossary in docs/ | ALPHA |

## PAIR CONTRACTS

W5-C1 (S2 both sides): ticket fingerprint = sha256(UA + IP)[:16] bound at mint;
upgrade rejects mismatch with 403 TICKET_FINGERPRINT_MISMATCH envelope. Client
sends X-WS-Fingerprint header computed identically.
W5-C2 (D1↔models sweep): every FK gains explicit ON DELETE (CASCADE for owned
children, SET NULL for optional refs, RESTRICT for audit integrity); the
deletion-cascade test suite enumerates every FK from metadata so drift fails.

## VERIFIER TEAMS

W5-VT1 → SECURITY (S1+S2): CSP enforce probe (script injection blocked,
legit loads pass), fingerprint mismatch rejection live probe.
W5-VT2 → CRYPTO (S4): re-encryption round-trip on seeded vault, dual-read
transition proof, no plaintext/plaintext-equivalent leakage.
W5-VT3 → DATA INTEGRITY (D1+D2): deletion-cascade enumeration vs metadata,
vacuum job execution evidence, runbook coherence.
W5-VT4 → API/DOCS (G6a+G6b+G7): CI guard trip-test (violation caught),
heuristic calibration corpus review, docs claims spot-checked against reality.
W5-VT5 → FRONTEND A11Y (S3): accessible-name audit via role= probing, login
320px layout verification.

## MERGE LAW

Same as W2–W4. Migration slots: 0022=S4, 0023=D1 (check ls first). ci.yml
edits = G6a only. crypto.py = S4 only (highest-care file; verifier pair
mandatory before merge).

# S1-R02 — Secret protection, key ownership, redaction, revocation

Status: COMPLETE (implemented, tested, verified — see §Verification results).
Register: `RISK_REGISTER.md` §R-02. Findings verified by code read (2 agents + spot-check).

## Findings (from register, confirmed)

- R-02a KDF divergence: S1 writes provider keys under the salted per-install KDF
  (`S1/.../crypto.py:295-321`) but S6 decrypts with the legacy unsalted stretch
  (`S6/.../ai_participant.py:237` → `vault.decrypt`). After `reencrypt_vault.py`
  runs, S6 can no longer read any key → `MalformedTeamKeyError` availability break.
- R-02b hardcoded dev-key fallback: `S6/.../service.py:98-103` builds a vault with
  the public dev constant when none is passed; deploy wires neither vault nor
  team_key_store for the meeting app → silent false confidence.
- R-02c engine env restore asymmetry: `S4/.../engine.py:219-236` sets
  `DEEPSEEK_API_KEY`/`DEEPSEEK_BASE_URL` in `os.environ` and only restores when a
  previous value existed — when originally unset, the secret persists in the
  server process env after the run.
- R-02d no master-key rotation path: `reencrypt_all_payloads` re-wraps under the
  SAME master key; no key-version column. True rotation unsupported.
- R-02e dev-key defaults in S1/S2/S8/S11 config; the one fail-fast is
  `DEP/runners/services.py:22-31` and has zero tests.
- R-02f no GitHub token revocation: OAuth reconnect deletes+reinserts
  (`S2/.../app.py:221`), but there is no user-facing disconnect.
- Already-mitigated (kept as-is): provider-key revoke endpoint + hint-only
  projection + audit; audit detail scrub; provider error scrub; sandbox env
  scrub; no committed credential literals.

## Threat model
Attacker: (T1) any network peer who can read process env / crash dumps after a run
(R-02c leak of provider key into server env); (T2) a user whose GitHub token stays
valid after they disconnect/offboard (R-02f); (T3) an operator who rotates
`HARNESS_MASTER_KEY` believing it is safe, bricking all ciphertext (R-02d);
(T4) silent-dev-key installations: prod running with the public constant gives
false confidence and, if any payloads were written under it, trivial decryption.
Assets: provider API keys (spend + abuse), GitHub OAuth tokens (repo write),
master key + all ciphertext.

## Fix design (smallest change per finding)

- R-02a: add a dual-read helper in `S6/ai_participant.py` that delegates to S1's
  canonical KDF read (`harness_identity.crypto.load_kdf_params` → decrypt with
  derived key first, legacy second) whenever the vault exposes `master_key`
  (S1 KeyVault and contracts KeyVault both carry it once wired from S1). Falls
  back to plain `vault.decrypt` only when S1's crypto module is unavailable
  (lean test env) or the vault has no `master_key` attribute. Failure still
  raises `MalformedTeamKeyError` (unchanged contract of this module).
- R-02b: replace the unconditional dev-key fallback with explicit configuration:
  * vault passed → use it;
  * else `HARNESS_MASTER_KEY` set → S1 `KeyVault(master)` (dual-read capable);
  * else `HARNESS_ALLOW_DEV_MASTER_KEY=1` → dev constant (local only);
  * else vault stays None → the existing no-key branch answers
    "not configured" honestly instead of attempting a bogus decrypt.
  Deploy: `app_meeting` wires a real `MeetingService(team_key_store, vault)`
  (additive `configure_meeting_service` in S6 api, mirroring `configure_sandbox`).
- R-02c: exact snapshot/restore of both env vars in `finally` (pop when
  originally unset) + regression test.
- R-02d: residual (needs key-versioning + migration → session 2). Documented in
  register + this note; rotation runbook warns API keys/GitHub tokens must be
  exported before rotating master key.
- R-02e: add deploy fail-fast test (subprocess import of `services.py` with env
  matrix) in section10; dev defaults in per-section configs stay (standalone dev
  only; assembly is the deployed gate) — residual documented.
- R-02f: add `POST /github/disconnect` in S2 (delete caller's
  `github_connections` rows, team/bearer-derived user, audit event when wired) +
  tests.

## Rollback
Per-fix revert; no schema changes; S6 vault construction and route additions are
additive. Removing deploy wiring restores previous (dev-fallback) behavior.

## Residuals
- R-02d master-key rotation needs key-versioning (session 2+); until then rotation
  = re-export keys → rotate → re-import.
- Per-section dev-key defaults remain for standalone development; deployed
  assembly is the enforced gate (fail-fast now tested).
- Access tokens remain long-lived GitHub OAuth tokens (scope-bounded); fine-grained
  token exchange is out of session scope.

## Verification results (R-02 COMPLETE — all six findings resolved or residualed)

Commands: `cd sectionN && ./.venv/bin/python -m pytest tests/ -q` and
`./.venv/bin/ruff check src tests` (per-section venvs; deploy file py_compile'd).

| Suite | Result |
|---|---|
| section1 | 628 passed, 10 skipped (unchanged) |
| section2 | 36 passed (+4: `tests/test_s1_r02_disconnect.py`) |
| section3 | 95 passed (unchanged) |
| section4 | 467 passed, 5 skipped (+2: env exact-restore tests in `tests/unit/test_engine.py`) |
| section5 | 162 passed, 1 skipped (unchanged) |
| section6 | 112 passed (+8: `tests/unit/test_s1_r02_secrets.py`; 1 updated malformed-key test now passes an explicit vault) |
| section8 | 86 passed (unchanged) |
| section10 | 69 passed (+4: `tests/unit/test_deploy_boot_gate.py`) |
| section11 | 50 passed — one flaky failure observed once (`test_index_lifecycle_over_http`, full-suite order), green on 4 consecutive reruns incl. the same full command; not traced to R-02 (section untouched). Watch item. |
| ruff | clean on sections 2, 4, 6, 10; `deploy/runners/services.py` parses (py_compile) |

Per-finding closure:
- R-02a: S6 decrypts via S1's `load_kdf_params` dual-read whenever the vault
  carries `master_key` (deploy now wires S1 `KeyVault`); legacy single-window
  path preserved for contracts vaults. Tests: derived-window, legacy-window,
  wrong-key honest error, contracts-vault back-compat.
- R-02b: S6 vault construction is explicit (passed → `HARNESS_MASTER_KEY` →
  `HARNESS_ALLOW_DEV_MASTER_KEY=1` → `None` = honest "not configured");
  deploy `app_meeting` wires `MeetingService(team_key_store, S1 KeyVault)` via
  new additive `configure_meeting_service`. Tests: gating matrix + store-without-vault
  answers not-configured.
- R-02c: exact env snapshot/restore in `finally` (both vars, pop when originally
  unset). Tests: originally-unset (with anti-vacuous in-run assertion) and
  previously-set restore.
- R-02e: deploy boot gate pinned by subprocess tests (missing key, dev-constant
  key → refuse; real key, explicit opt-in → boot).
- R-02f: `POST /api/v1/github/disconnect` deletes the caller's stored tokens and
  reports `removed`; only caller's rows, auth required. Tests: removal scoped,
  status flips, idempotent, 401 unauthenticated.
- R-02d: residual (key-versioning schema needed) — register row updated.

Notes:
- `section6/pyproject.toml` test `pythonpath` extended with `../section1/src` so
  the dual-read tests can drive S1's crypto helpers; test-only, no runtime coupling.
- S6 venv standalone keeps the documented lean-env fallback (no `harness_identity`
  at runtime → plain vault decrypt); the deployed image installs Section 1.
- No commits made (working tree intentionally still dirty — review pending).

## R-02a revision — KDF derivation promoted to contracts (post-session-2 review)

Trigger: the session-2 verification battery ran `make static-gates` and found
`GUARD[isolation]` RED — 2 violations introduced by R-02a's original runtime
imports (`service.py`: package-name import of `harness_identity.crypto`;
`ai_participant.py`: tree import of `load_kdf_params`). R-02's original
verification ran suites + ruff but never the static gates — process gap.
Per M17-B3 (`docs/ci-gates.md`), sections share via `contracts/` only;
cross-section data reads go through raw SQL against frozen tables.

Fix (R-02a revision, additive — no behavior change):
- `harness_contracts.security` now owns the KDF (constants +
  `kdf_algo_id`/`derive_fernet_key`/`derive_fernet`/`legacy_fernet`).
- `harness_identity.crypto` delegates to contracts (private aliases keep its
  surface); `HAS_ARGON2` is imported from contracts in tests now.
- S6: `EnvMasterKeyVault(KeyVault)` carries the `master_key` marker;
  `_decrypt_stored_key`/`_load_kdf_params` rebuild both windows from the frozen
  `key_derivation_params` row via raw SQL; wrong-key error text identical to S1.
- Contracts version bumped `1.0.0rc1` → `1.0.0rc2`, atomic 4-way pin
  (pyproject + `__init__.__version__` + `types.d.ts` header + editable install
  re-provisioned in the section1 venv); `generate.py --check` clean; consumer
  floor pins satisfiable. No TS codegen impact (`security` is not a generated module).

Evidence (all green, run 2026-10-17):
- `make guard-isolation` → OK (163 src files, 10 sections, 0 edges).
- `make static-gates` → green (entrypoint syntax + compose config + TS mirror).
- `make test` (full, sequential): section1 633/10, section2 36, section3 95,
  section4 482/5, section5 176/1, section6 118, section8 88, section10 69.
- `make lint-section1/4/6` clean; `tests/test_kdf_upgrade.py` 12 passed.
- S6 R-02 suite now 10 tests (2 new: derived-window rebuild from a params row,
  no-row legacy-only); dual-read trio monkeypatches the local helper — zero
  S1↔S6 runtime coupling remains. `../section1/src` pythonpath stays for
  test-only parity suites (tz/ratelimit/request-size).
- Watch item: one S1 failure observed when suites were run in parallel
  (not reproducible sequentially; timing-sensitive WS tests — not traced to
  this change).

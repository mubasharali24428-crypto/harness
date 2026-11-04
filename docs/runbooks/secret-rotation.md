# Secret rotation

Covers every secret the platform stores or shares. Only the master-key step has
an offline script; the rest are API operations or restarts.

## Master key (`HARNESS_MASTER_KEY`)

The master key derives the Fernet key that encrypts, at rest:

| Table | Column | Written by |
|---|---|---|
| `api_keys` | `key_encrypted` | model provider keys |
| `integration_webhooks` | `secret_encrypted` | webhook signing secrets |
| `github_connections` | `access_token_encrypted` | GitHub OAuth tokens |
| `team_sso_configs` | `client_secret_encrypted` | SSO client secrets |

`ENCRYPTED_COLUMNS` in `section1/src/harness_identity/crypto.py` is the single
declared list; `test_master_key_rotation.py` pins it against the models. A new
encrypted column must be added there or rotation will silently skip it.

### Procedure

1. **Preflight (no writes).** Point the old key at the live database:

   ```sh
   cd section1
   HARNESS_OLD_MASTER_KEY="$HARNESS_MASTER_KEY" HARNESS_NEW_MASTER_KEY="$(new-key)" \
     ./.venv/bin/python scripts/rotate_master_key.py --dry-run
   ```

   This decrypts every row with the old key and reports
   `{"total", "readable", "rewrapped": 0, "failed"}` per table. **Do not
   proceed if `failed` is non-zero** — those rows are already unreadable, and
   rotation will not fix them.

2. **Rotate** (same command without `--dry-run`). Each row is decrypted with the
   old key's read set (salted, then the legacy SHA-256 derivation), encrypted
   with the new key, and read back before it is written. A row that fails the
   read-back is counted and left byte-identical.

3. **Deploy the new key.** Restart every service that reads
   `HARNESS_MASTER_KEY` (identity, orchestrator, meeting, review, github,
   sandbox). Until they restart, they still hold the old key.

4. **Verify.** Re-run the dry-run with the old key. It should now report
   `failed = <row count>` (the old key can no longer read anything) and a
   dry-run with the **new** key should report `failed = 0`. Then exercise one
   decrypting path: a model call with a stored team key, or a webhook test
   delivery.

### Writes during rotation

There is a window between step 2 and step 3. A secret written by a service
still holding the old key **after** it restarted on the new key is
undecryptable. Two acceptable postures:

* **Quiesce writes**: pause run starts and webhook/SSO/API-key edits for the
  window (minutes on a small install).
* **Accept and detect**: after the new key is live, run the dry-run with the
  OLD key — any row it can still read was written in the window. Re-run the
  rotation restricted to those rows (the script reports them as `failed` on a
  second pass with the new key, so identify them by a decrypt probe before
  deciding whether to reset them through their owning API).

### Rollback

Keep the old key until verification passes. Rolling back means running the same
script with the NEW key as `--old-key-env` and the OLD key as `--new-key-env`,
then redeploying the old key. A payload written after step 3 cannot be rolled
back to a key the writing service never held; reset those secrets through their
APIs.

## Runner tokens

`POST /api/v1/team-runners/{id}/rotate` mints a new token, prints it **once**,
and invalidates the old hash immediately. The runner must be updated in the
same operation or its next request answers 401. Revoke a runner with
`POST /api/v1/team-runners/{id}/revoke`; revocation is checked before anything
else.

## Webhook signing secrets

`POST /api/v1/teams/{team_id}/settings/integrations/v1/webhooks/{webhook_id}/rotate-secret`
returns the new secret once and bumps the webhook version. The receiver must
accept it before the next delivery; there is no dual-secret window.

## Worker-daemon token (`HARNESS_WORKER_DAEMON_TOKEN`)

One shared value between the orchestrator and the worker daemon. Rotation is a
coordinated restart, not an API call:

1. Generate a new token.
2. Set it on **both** services (compose env) and restart them together.
3. Confirm one run reaches a worker (a drill run is enough).

A partial restart means every worker call answers 401 and runs fail closed —
which is the safe posture, but it is an outage, so treat it as a maintenance
window.

## API provider keys

No in-place rotation. Add the new key (`POST /api/v1/keys`), run one model call
against it, then delete the old key (`DELETE /api/v1/keys/{key_id}`). Both keys
are stored encrypted under the same master key.

## Cadence

| Secret | Recommended cadence | Trigger |
|---|---|---|
| Master key | quarterly, or on any staff with key access leaving | leak suspicion |
| Worker-daemon token | quarterly, and after any host rebuild | leak suspicion |
| Runner tokens | per runner lifetime / on decommission | revocation |
| Webhook secrets | on receiver change or leak | leak suspicion |
| Provider keys | per provider policy | provider rotation notice |

## Evidence

`test_master_key_rotation.py` proves, on a scratch database: all four columns
are re-wrapped, a legacy-derived payload converts, the old key stops reading,
an unreadable row is counted and untouched, a dry-run writes nothing, and the
same key is refused. Live rotation against the compose database was **not**
performed (it would invalidate the running stack's secrets); the runbook is the
procedure for an operator to do so in a maintenance window.

# EXAMPLE — M10 node A1 retrofitted to command-brief format

INTENT — so that users can store provider keys safely per team, deliver a BYO-keys
API such that mint→list→revoke works live and no raw key ever leaves the vault.

## END-STATE

- `section1/tests/test_api_keys_routes.py` passes 9/9 in the section1 venv.
- Live probe: POST /api/v1/keys mints → GET lists with keyHint only → DELETE
  soft-revokes; full key never appears in any subsequent response payload.
- Ruff clean on owned files; footprint == owned paths exactly.

## CONSTRAINTS

- contracts/, conftest.py FROZEN. Migration numbering: next free slot,
  re-checked immediately before naming. No new dependencies. No section imports
  outside harness_contracts. Encrypt via existing KeyVault crypto; hint per
  convention; soft revoke sets revoked_at. Siblings' dirty files expected —
  never revert them.

## OWNERSHIP

- section1/src/harness_identity/api_keys.py (NEW)
- section1/tests/test_api_keys_routes.py (NEW)
- models.py append-if-missing (ApiKey ORM class ONLY)

## BACKBRIEF

- Intent in my own words: give teams a safe CRUD home for their own provider
  keys where the plaintext key is write-only and revocation is soft.
- Paths I will write: api_keys.py, test_api_keys_routes.py, models.py append-only.
- Frozen things I will NOT touch: contracts/, conftest.py, other sections' files,
  existing migrations.
- My done-proof will be: 9/9 pytest in section1 venv + live ASGI mint/list/
  revoke walk asserting hint-not-key + ruff clean, pasted raw into my report.

---

*Provenance note: this file retrofits node A1 from docs/briefs/mission10-graph.md
into doctrine format (§1–§3) to prove legacy briefs convert without loss.
Verify: python3 scripts/doctrine/check_doctrine.py docs/doctrine/examples → OK.*

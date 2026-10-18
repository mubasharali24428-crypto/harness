"""Ed25519-signed catalog of vetted extensions. Nothing is installable unless verified.

Document: {"payload": "<canonical JSON text>", "signature": "<hex>"}. The signature covers the
exact payload text, so verification never depends on re-serialisation.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class CatalogRejected(ValueError):
    pass


def sign_catalog(entries: list[dict[str, Any]], key: Ed25519PrivateKey) -> dict[str, str]:
    payload = json.dumps({"entries": entries}, sort_keys=True, separators=(",", ":"))
    return {"payload": payload, "signature": key.sign(payload.encode()).hex()}


def verify_catalog(doc: Any, public_key_hex: str | None) -> list[dict[str, Any]]:
    """Return the entries of a validly signed catalog, else raise CatalogRejected."""
    if not public_key_hex:
        raise CatalogRejected("no catalog signing key configured")
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(doc["signature"]), doc["payload"].encode())
        entries = json.loads(doc["payload"])["entries"]
    except (InvalidSignature, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CatalogRejected("catalog signature or format is invalid") from exc
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise CatalogRejected("catalog entries are malformed")
    return entries

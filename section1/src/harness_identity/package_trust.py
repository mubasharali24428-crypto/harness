"""A1-S4 — signed agent/plugin package verification (F097). Pure; no I/O.

A signature binds the canonical manifest {name, version, sha256, issuer, keyId,
issuedAt[, notAfter]} under an Ed25519 key. Trust therefore requires ALL of:
  1. the manifest's sha256 equals the digest of the package bytes presented,
  2. keyId resolves to an ACTIVE key in the team's trust store whose issuer
     equals the manifest's issuer (substitution of issuer/version/bytes fails),
  3. the signature verifies over the canonical manifest,
  4. the manifest is not expired and neither (name, version) nor sha256 is revoked.
An unsigned or unknown-issuer package is never "trusted" — there is no
allow-by-default catalog.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

REQUIRED = ("name", "version", "sha256", "issuer", "keyId", "issuedAt")
MAX_FIELD = 200


@dataclass(frozen=True)
class IssuerKey:
    issuer: str
    public_key_b64: str
    active: bool


def canonical_manifest(manifest: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(manifest), sort_keys=True, separators=(",", ":")).encode()


def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def public_key_b64(private: Ed25519PrivateKey) -> str:
    return b64e(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def sign_manifest(private: Ed25519PrivateKey, manifest: Mapping[str, Any]) -> str:
    """Issuer-side helper (used by tooling and tests; the server never signs)."""
    return b64e(private.sign(canonical_manifest(manifest)))


def valid_public_key(b64: str) -> bool:
    try:
        return len(base64.b64decode(b64, validate=True)) == 32
    except ValueError:
        return False


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_package(
    manifest: Any,
    signature_b64: Any,
    *,
    content_sha256: str,
    issuers: Mapping[str, IssuerKey],
    revoked_name_versions: set[tuple[str, str]] = frozenset(),  # type: ignore[assignment]
    revoked_digests: set[str] = frozenset(),  # type: ignore[assignment]
    now: datetime | None = None,
) -> dict[str, Any]:
    def no(reason: str) -> dict[str, Any]:
        return {"trusted": False, "reason": reason}

    if not isinstance(manifest, dict) or not isinstance(signature_b64, str):
        return no("package.malformed")
    for key in REQUIRED:
        v = manifest.get(key)
        if not isinstance(v, str) or not v or len(v) > MAX_FIELD:
            return no("package.malformed")
    if manifest["sha256"].lower() != content_sha256.lower():
        return no("package.digest_mismatch")
    entry = issuers.get(manifest["keyId"])
    if entry is None:
        return no("package.unknown_key")
    if not entry.active:
        return no("package.issuer_revoked")
    if entry.issuer != manifest["issuer"]:
        return no("package.issuer_mismatch")
    try:
        sig = base64.b64decode(signature_b64, validate=True)
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(entry.public_key_b64))
        pub.verify(sig, canonical_manifest(manifest))
    except (ValueError, InvalidSignature):
        return no("package.bad_signature")
    not_after = manifest.get("notAfter")
    if not_after is not None:
        try:
            deadline = datetime.fromisoformat(not_after)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return no("package.malformed")
        if (now or datetime.now(UTC)) >= deadline:
            return no("package.expired")
    if (manifest["name"], manifest["version"]) in revoked_name_versions or (
        manifest["sha256"].lower() in revoked_digests
    ):
        return no("package.revoked")
    return {
        "trusted": True,
        "reason": "package.verified",
        "issuer": manifest["issuer"],
        "name": manifest["name"],
        "version": manifest["version"],
    }

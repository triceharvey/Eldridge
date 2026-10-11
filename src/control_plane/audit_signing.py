"""Detached Ed25519 signatures for portable audit bundles.

A bundle digest proves integrity only relative to a trusted copy of that digest. A signature
moves the trust anchor to a public key: anyone holding the operator's public key can confirm that
the holder of the matching private key attested to this exact bundle, and a rewritten bundle with
a recomputed digest no longer verifies. The private key never enters the control plane's database
or runtime; signing is an explicit operator step on an already exported, already verified bundle.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from control_plane.audit_export import _canonical_json, verify_audit_export

SIGNATURE_SCHEMA_VERSION = "1"
ALGORITHM = "ed25519"
_SIGNED_FIELDS = (
    "schema_version",
    "algorithm",
    "bundle_digest",
    "workflow_id",
    "key_id",
    "signed_at",
)


def load_private_key(pem: bytes) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (TypeError, ValueError) as error:
        raise ValueError("signing key is not an unencrypted PEM private key") from error
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing key must be Ed25519")
    return key


def load_public_key(pem: bytes) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(pem)
    except ValueError as error:
        raise ValueError("verification key is not a PEM public key") from error
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("verification key must be Ed25519")
    return key


def key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return f"sha256:{sha256(raw).hexdigest()}"


def _signed_message(envelope: Mapping[str, object]) -> bytes:
    return _canonical_json({field: envelope.get(field) for field in _SIGNED_FIELDS})


def sign_audit_export(
    bundle: object,
    private_key: Ed25519PrivateKey,
    *,
    signed_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a detached signature envelope for a bundle that already verifies."""

    if not verify_audit_export(bundle):
        raise ValueError("refusing to sign an audit bundle that fails verification")
    assert isinstance(bundle, Mapping)
    timestamp = (signed_at or datetime.now(UTC)).astimezone(UTC)
    envelope: dict[str, Any] = {
        "schema_version": SIGNATURE_SCHEMA_VERSION,
        "algorithm": ALGORITHM,
        "bundle_digest": bundle["bundle_digest"],
        "workflow_id": bundle["workflow"]["id"],
        "key_id": key_id(private_key.public_key()),
        "signed_at": timestamp.isoformat(),
    }
    signature = private_key.sign(_signed_message(envelope))
    envelope["signature"] = base64.b64encode(signature).decode("ascii")
    return envelope


def verify_signed_audit_export(
    bundle: object,
    envelope: object,
    public_key: Ed25519PublicKey,
) -> bool:
    """Verify the bundle itself, its binding to the envelope, and the envelope's signature."""

    if not verify_audit_export(bundle) or not isinstance(envelope, Mapping):
        return False
    assert isinstance(bundle, Mapping)
    workflow = bundle["workflow"]
    if (
        envelope.get("schema_version") != SIGNATURE_SCHEMA_VERSION
        or envelope.get("algorithm") != ALGORITHM
        or envelope.get("bundle_digest") != bundle.get("bundle_digest")
        or envelope.get("workflow_id") != workflow.get("id")
        or envelope.get("key_id") != key_id(public_key)
        or not isinstance(envelope.get("signed_at"), str)
    ):
        return False
    encoded = envelope.get("signature")
    if not isinstance(encoded, str):
        return False
    try:
        signature = base64.b64decode(encoded, validate=True)
        public_key.verify(signature, _signed_message(envelope))
    except (binascii.Error, InvalidSignature):
        return False
    return True

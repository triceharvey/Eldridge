from __future__ import annotations

import copy
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from control_plane.audit_export import _bundle_digest, build_audit_export
from control_plane.audit_signing import (
    key_id,
    load_private_key,
    load_public_key,
    sign_audit_export,
    verify_signed_audit_export,
)
from control_plane.cli import main
from control_plane.service import ControlPlaneService


def _bundle(service: ControlPlaneService) -> dict[str, object]:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Signed audit evidence",
        description="Exercise detached signatures over a portable audit bundle.",
        idempotency_key="signed-audit-export",
        repository_scope="eldridge/local",
    )
    with service.session_factory() as session:
        return build_audit_export(
            session, str(workflow["id"]), exported_at=datetime(2026, 10, 10, tzinfo=UTC)
        )


def _pem(key: Ed25519PrivateKey) -> tuple[bytes, bytes]:
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


def test_signature_binds_bundle_workflow_and_key(service: ControlPlaneService) -> None:
    bundle = _bundle(service)
    key = Ed25519PrivateKey.generate()
    envelope = sign_audit_export(bundle, key, signed_at=datetime(2026, 10, 10, tzinfo=UTC))

    assert envelope["algorithm"] == "ed25519"
    assert envelope["bundle_digest"] == bundle["bundle_digest"]
    assert envelope["key_id"] == key_id(key.public_key())
    assert verify_signed_audit_export(bundle, envelope, key.public_key())


def test_rewritten_bundle_with_recomputed_digest_fails(service: ControlPlaneService) -> None:
    bundle = _bundle(service)
    key = Ed25519PrivateKey.generate()
    envelope = sign_audit_export(bundle, key)

    forged = copy.deepcopy(bundle)
    forged["exported_at"] = "2030-01-01T00:00:00+00:00"
    body = {k: v for k, v in forged.items() if k != "bundle_digest"}
    forged["bundle_digest"] = _bundle_digest(body)

    from control_plane.audit_export import verify_audit_export

    assert verify_audit_export(forged)  # internally consistent on its own
    assert not verify_signed_audit_export(forged, envelope, key.public_key())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signed_at", "2030-01-01T00:00:00+00:00"),
        ("workflow_id", "another-workflow"),
        ("algorithm", "rsa"),
        ("schema_version", "2"),
        ("signature", "not base64!"),
        ("signature", None),
        ("signed_at", None),
    ],
)
def test_envelope_tampering_fails(service: ControlPlaneService, field: str, value: object) -> None:
    bundle = _bundle(service)
    key = Ed25519PrivateKey.generate()
    envelope = sign_audit_export(bundle, key)
    envelope[field] = value
    assert not verify_signed_audit_export(bundle, envelope, key.public_key())


def test_wrong_key_and_malformed_inputs_fail(service: ControlPlaneService) -> None:
    bundle = _bundle(service)
    key = Ed25519PrivateKey.generate()
    envelope = sign_audit_export(bundle, key)
    other = Ed25519PrivateKey.generate().public_key()
    assert not verify_signed_audit_export(bundle, envelope, other)
    assert not verify_signed_audit_export(bundle, "not an envelope", key.public_key())
    assert not verify_signed_audit_export({"bundle_digest": "x"}, envelope, key.public_key())


def test_refuses_to_sign_invalid_bundle(service: ControlPlaneService) -> None:
    bundle = _bundle(service)
    bundle["exported_at"] = "tampered"
    with pytest.raises(ValueError, match="fails verification"):
        sign_audit_export(bundle, Ed25519PrivateKey.generate())


def test_key_loading_rejects_wrong_types() -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    ec_key = ec.generate_private_key(ec.SECP256R1())
    ec_private = ec_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    ec_public = ec_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with pytest.raises(ValueError, match="must be Ed25519"):
        load_private_key(ec_private)
    with pytest.raises(ValueError, match="must be Ed25519"):
        load_public_key(ec_public)
    with pytest.raises(ValueError, match="unencrypted PEM"):
        load_private_key(b"garbage")
    with pytest.raises(ValueError, match="PEM public key"):
        load_public_key(b"garbage")


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int | str | None:
    monkeypatch.setattr(sys, "argv", ["control-plane", "audit", *argv])
    with pytest.raises(SystemExit) as exit_info:
        main()
    return exit_info.value.code


def test_cli_signs_and_verifies(
    service: ControlPlaneService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_path = tmp_path / "audit.json"
    bundle_path.write_text(json.dumps(_bundle(service)), encoding="utf-8")
    private, public = _pem(Ed25519PrivateKey.generate())
    private_path = tmp_path / "signing.pem"
    private_path.write_bytes(private)
    public_path = tmp_path / "signing.pub.pem"
    public_path.write_bytes(public)
    signature_path = tmp_path / "audit.sig.json"

    private_path.chmod(0o644)
    assert "group or others" in str(
        _run(
            monkeypatch,
            "sign",
            "--bundle",
            str(bundle_path),
            "--private-key",
            str(private_path),
            "--output",
            str(signature_path),
        )
    )
    private_path.chmod(0o600)
    sign = ("sign", "--bundle", str(bundle_path), "--private-key", str(private_path))
    assert _run(monkeypatch, *sign, "--output", str(signature_path)) == 0
    assert _run(monkeypatch, *sign, "--output", str(signature_path)) == (
        "audit signature output already exists"
    )
    assert _run(monkeypatch, *sign, "--output", str(tmp_path / "missing" / "x.json")) == (
        "audit signature parent directory does not exist"
    )

    verify = ("verify", "--bundle", str(bundle_path))
    assert _run(monkeypatch, *verify) == 0
    assert (
        _run(
            monkeypatch,
            *verify,
            "--signature",
            str(signature_path),
            "--public-key",
            str(public_path),
        )
        == 0
    )
    assert _run(monkeypatch, *verify, "--signature", str(signature_path)) == (
        "--signature and --public-key must be given together"
    )

    other_public = tmp_path / "other.pub.pem"
    other_public.write_bytes(_pem(Ed25519PrivateKey.generate())[1])
    assert (
        _run(
            monkeypatch,
            *verify,
            "--signature",
            str(signature_path),
            "--public-key",
            str(other_public),
        )
        == 2
    )
    assert "PEM public key" in str(
        _run(
            monkeypatch,
            *verify,
            "--signature",
            str(signature_path),
            "--public-key",
            str(private_path),
        )
    )
    assert _run(monkeypatch, "verify", "--bundle", str(tmp_path / "nope.json")) == (
        "audit bundle is unreadable or invalid JSON"
    )

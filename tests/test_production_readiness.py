import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from control_plane.cli import main
from control_plane.production_readiness import (
    evaluate_production_readiness,
    evaluate_production_readiness_file,
)

NOW = datetime(2026, 9, 13, 22, 0, tzinfo=UTC)
REVISION = "a" * 40
IMAGE_DIGEST = "@sha256:" + "b" * 64


def _valid_evidence() -> dict[str, object]:
    observed_at = "2026-09-13T21:00:00Z"
    return {
        "schema_version": "1",
        "environment": "production",
        "release_revision": REVISION,
        "immutable_images": {
            "api_image": f"registry.eldridge.dev/control-plane-api{IMAGE_DIGEST}",
            "worker_image": f"registry.eldridge.dev/control-plane-worker{IMAGE_DIGEST}",
            "ingress_image": f"registry.eldridge.dev/caddy{IMAGE_DIGEST}",
            "scan_passed": True,
            "critical_findings": 0,
            "high_findings": 0,
            "observed_at": observed_at,
        },
        "public_tls": {
            "endpoint": "https://control.eldridge.dev",
            "dns_resolves": True,
            "certificate_valid": True,
            "minimum_tls_version": "1.3",
            "observed_at": observed_at,
        },
        "oidc": {
            "issuer": "https://identity.eldridge.dev",
            "audience": "eldridge-production",
            "jwks_uri": "https://identity.eldridge.dev/.well-known/jwks.json",
            "mapped_subject": "operator:triceharvey",
            "positive_test_passed": True,
            "negative_test_passed": True,
            "observed_at": observed_at,
        },
        "postgresql": {
            "host": "postgres.eldridge.dev",
            "tls_verified": True,
            "migration_at_head": True,
            "connectivity_test_passed": True,
            "observed_at": observed_at,
        },
        "github_webhook": {
            "delivery_id": "delivery-123",
            "revision": REVISION,
            "signature_verified": True,
            "duplicate_delivery_test_passed": True,
            "observed_at": observed_at,
        },
        "metrics": {
            "authenticated_scrape_passed": True,
            "unauthorized_scrape_rejected": True,
            "observed_at": observed_at,
        },
        "backup_restore": {
            "backup_id": "backup-123",
            "backup_at": "2026-09-13T20:00:00Z",
            "restore_at": observed_at,
            "restored_revision": REVISION,
            "restore_verified": True,
            "measured_rpo_seconds": 60,
            "measured_rto_seconds": 120,
        },
        "protected_workflow": {
            "workflow_id": "workflow-123",
            "pull_request_number": 37,
            "head_revision": REVISION,
            "merge_commit": "c" * 40,
            "direct_push_blocked": True,
            "required_checks": ["gitleaks", "package", "test"],
            "human_approval_recorded": True,
            "observed_at": observed_at,
        },
    }


def test_complete_recent_bound_evidence_is_ready() -> None:
    report = evaluate_production_readiness(_valid_evidence(), now=NOW)

    assert report["ready"] is True
    assert report["blocker_count"] == 0
    assert report["blockers"] == []
    assert report["manifest_digest"].startswith("sha256:")


def test_cross_revision_and_failed_outcomes_block_readiness() -> None:
    evidence = _valid_evidence()
    webhook = evidence["github_webhook"]
    assert isinstance(webhook, dict)
    webhook["revision"] = "d" * 40
    metrics = evidence["metrics"]
    assert isinstance(metrics, dict)
    metrics["unauthorized_scrape_rejected"] = False

    report = evaluate_production_readiness(evidence, now=NOW)

    assert report["ready"] is False
    assert "github_webhook: revision does not match release_revision" in report["blockers"]
    assert "metrics.unauthorized_scrape_rejected: required check did not pass" in report["blockers"]


def test_invalid_or_placeholder_evidence_returns_redacted_blockers() -> None:
    evidence = deepcopy(_valid_evidence())
    images = evidence["immutable_images"]
    assert isinstance(images, dict)
    images["api_image"] = "registry.example.com/api:latest"
    oidc = evidence["oidc"]
    assert isinstance(oidc, dict)
    oidc["issuer"] = "https://user:secret@identity.example.com"

    report = evaluate_production_readiness(evidence, now=NOW)

    assert report["ready"] is False
    assert report["manifest_digest"] is None
    encoded = json.dumps(report)
    assert "secret" not in encoded
    assert any(blocker.startswith("immutable_images.api_image:") for blocker in report["blockers"])
    assert any(blocker.startswith("oidc.issuer:") for blocker in report["blockers"])


def test_stale_and_future_evidence_fail_closed() -> None:
    evidence = _valid_evidence()
    tls = evidence["public_tls"]
    assert isinstance(tls, dict)
    tls["observed_at"] = "2026-07-01T00:00:00Z"
    metrics = evidence["metrics"]
    assert isinstance(metrics, dict)
    metrics["observed_at"] = "2026-09-14T00:00:00Z"

    report = evaluate_production_readiness(evidence, now=NOW)

    assert "public_tls: evidence is older than 30 days" in report["blockers"]
    assert "metrics: evidence timestamp is in the future" in report["blockers"]


def test_file_size_and_json_are_bounded(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json")
    with pytest.raises(ValueError, match="invalid JSON"):
        evaluate_production_readiness_file(invalid)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * 65_537)
    with pytest.raises(ValueError, match="size limit"):
        evaluate_production_readiness_file(oversized)


def test_cli_returns_two_and_machine_readable_blockers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = Path(__file__).parents[1] / "production-readiness.example.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["control-plane", "production", "readiness", "--manifest", str(manifest)],
    )

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False
    assert report["blocker_count"] >= 8

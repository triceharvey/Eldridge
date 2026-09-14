from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
)

SHA256_IMAGE_PATTERN = r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$"
REVISION_PATTERN = r"^[0-9a-f]{40}$"


class StrictEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImmutableImagesEvidence(StrictEvidenceModel):
    api_image: str = Field(pattern=SHA256_IMAGE_PATTERN)
    worker_image: str = Field(pattern=SHA256_IMAGE_PATTERN)
    ingress_image: str = Field(pattern=SHA256_IMAGE_PATTERN)
    scan_passed: StrictBool
    critical_findings: int = Field(ge=0, strict=True)
    high_findings: int = Field(ge=0, strict=True)
    observed_at: AwareDatetime


class PublicTlsEvidence(StrictEvidenceModel):
    endpoint: str
    dns_resolves: StrictBool
    certificate_valid: StrictBool
    minimum_tls_version: str
    observed_at: AwareDatetime

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("endpoint must be a credential-free HTTPS URL")
        if parsed.hostname == "example.com" or parsed.hostname.endswith(".example.com"):
            raise ValueError("endpoint must not use an example.com placeholder")
        return value


class OidcEvidence(StrictEvidenceModel):
    issuer: str
    audience: str = Field(min_length=1, max_length=200)
    jwks_uri: str
    mapped_subject: str = Field(min_length=1, max_length=200)
    positive_test_passed: StrictBool
    negative_test_passed: StrictBool
    observed_at: AwareDatetime

    @field_validator("issuer", "jwks_uri")
    @classmethod
    def validate_https_identity_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("identity URLs must use credential-free HTTPS")
        if parsed.hostname == "example.com" or parsed.hostname.endswith(".example.com"):
            raise ValueError("identity URLs must not use an example.com placeholder")
        return value


class PostgreSqlEvidence(StrictEvidenceModel):
    host: str = Field(min_length=1, max_length=253)
    tls_verified: StrictBool
    migration_at_head: StrictBool
    connectivity_test_passed: StrictBool
    observed_at: AwareDatetime

    @field_validator("host")
    @classmethod
    def reject_database_credentials(cls, value: str) -> str:
        if "://" in value or "@" in value or value == "localhost":
            raise ValueError("host must be a non-local hostname without credentials")
        if value == "example.com" or value.endswith(".example.com"):
            raise ValueError("host must not use an example.com placeholder")
        return value


class WebhookEvidence(StrictEvidenceModel):
    delivery_id: str = Field(min_length=1, max_length=200)
    revision: str = Field(pattern=REVISION_PATTERN)
    signature_verified: StrictBool
    duplicate_delivery_test_passed: StrictBool
    observed_at: AwareDatetime


class MetricsEvidence(StrictEvidenceModel):
    authenticated_scrape_passed: StrictBool
    unauthorized_scrape_rejected: StrictBool
    observed_at: AwareDatetime


class BackupRestoreEvidence(StrictEvidenceModel):
    backup_id: str = Field(min_length=1, max_length=200)
    backup_at: AwareDatetime
    restore_at: AwareDatetime
    restored_revision: str = Field(pattern=REVISION_PATTERN)
    restore_verified: StrictBool
    measured_rpo_seconds: int = Field(ge=0, le=86_400, strict=True)
    measured_rto_seconds: int = Field(ge=0, le=3_600, strict=True)


class ProtectedWorkflowEvidence(StrictEvidenceModel):
    workflow_id: str = Field(min_length=1, max_length=200)
    pull_request_number: int = Field(gt=0, strict=True)
    head_revision: str = Field(pattern=REVISION_PATTERN)
    merge_commit: str = Field(pattern=REVISION_PATTERN)
    direct_push_blocked: StrictBool
    required_checks: tuple[str, ...] = Field(min_length=1, max_length=20)
    human_approval_recorded: StrictBool
    observed_at: AwareDatetime


class ProductionReadinessEvidence(StrictEvidenceModel):
    schema_version: str = Field(pattern=r"^1$")
    environment: str = Field(pattern=r"^production$")
    release_revision: str = Field(pattern=REVISION_PATTERN)
    immutable_images: ImmutableImagesEvidence
    public_tls: PublicTlsEvidence
    oidc: OidcEvidence
    postgresql: PostgreSqlEvidence
    github_webhook: WebhookEvidence
    metrics: MetricsEvidence
    backup_restore: BackupRestoreEvidence
    protected_workflow: ProtectedWorkflowEvidence


def _canonical_digest(evidence: ProductionReadinessEvidence) -> str:
    encoded = json.dumps(
        evidence.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validation_blockers(error: ValidationError) -> list[str]:
    blockers = []
    for issue in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in issue["loc"])
        blockers.append(f"{location}: {issue['msg']}")
    return sorted(blockers)


def _freshness_blockers(
    evidence: ProductionReadinessEvidence,
    *,
    now: datetime,
) -> list[str]:
    maximum_age = timedelta(days=30)
    future_tolerance = timedelta(minutes=5)
    timestamps = {
        "immutable_images": evidence.immutable_images.observed_at,
        "public_tls": evidence.public_tls.observed_at,
        "oidc": evidence.oidc.observed_at,
        "postgresql": evidence.postgresql.observed_at,
        "github_webhook": evidence.github_webhook.observed_at,
        "metrics": evidence.metrics.observed_at,
        "backup_restore": evidence.backup_restore.restore_at,
        "protected_workflow": evidence.protected_workflow.observed_at,
    }
    blockers = []
    for gate, observed_at in timestamps.items():
        if observed_at > now + future_tolerance:
            blockers.append(f"{gate}: evidence timestamp is in the future")
        elif now - observed_at > maximum_age:
            blockers.append(f"{gate}: evidence is older than 30 days")
    return blockers


def _binding_blockers(evidence: ProductionReadinessEvidence) -> list[str]:
    blockers = []
    if evidence.github_webhook.revision != evidence.release_revision:
        blockers.append("github_webhook: revision does not match release_revision")
    if evidence.backup_restore.restored_revision != evidence.release_revision:
        blockers.append("backup_restore: restored_revision does not match release_revision")
    if evidence.protected_workflow.head_revision != evidence.release_revision:
        blockers.append("protected_workflow: head_revision does not match release_revision")
    if evidence.backup_restore.restore_at < evidence.backup_restore.backup_at:
        blockers.append("backup_restore: restore_at precedes backup_at")
    if len(set(evidence.protected_workflow.required_checks)) != len(
        evidence.protected_workflow.required_checks
    ):
        blockers.append("protected_workflow: required_checks contains duplicates")
    return blockers


def _outcome_blockers(evidence: ProductionReadinessEvidence) -> list[str]:
    checks = {
        "immutable_images.scan_passed": evidence.immutable_images.scan_passed,
        "public_tls.dns_resolves": evidence.public_tls.dns_resolves,
        "public_tls.certificate_valid": evidence.public_tls.certificate_valid,
        "oidc.positive_test_passed": evidence.oidc.positive_test_passed,
        "oidc.negative_test_passed": evidence.oidc.negative_test_passed,
        "postgresql.tls_verified": evidence.postgresql.tls_verified,
        "postgresql.migration_at_head": evidence.postgresql.migration_at_head,
        "postgresql.connectivity_test_passed": evidence.postgresql.connectivity_test_passed,
        "github_webhook.signature_verified": evidence.github_webhook.signature_verified,
        "github_webhook.duplicate_delivery_test_passed": (
            evidence.github_webhook.duplicate_delivery_test_passed
        ),
        "metrics.authenticated_scrape_passed": evidence.metrics.authenticated_scrape_passed,
        "metrics.unauthorized_scrape_rejected": evidence.metrics.unauthorized_scrape_rejected,
        "backup_restore.restore_verified": evidence.backup_restore.restore_verified,
        "protected_workflow.direct_push_blocked": (evidence.protected_workflow.direct_push_blocked),
        "protected_workflow.human_approval_recorded": (
            evidence.protected_workflow.human_approval_recorded
        ),
    }
    blockers = [
        f"{name}: required check did not pass" for name, passed in checks.items() if not passed
    ]
    if evidence.immutable_images.critical_findings != 0:
        blockers.append("immutable_images: critical vulnerability findings must be zero")
    if evidence.immutable_images.high_findings != 0:
        blockers.append("immutable_images: high vulnerability findings must be zero")
    if evidence.public_tls.minimum_tls_version not in {"1.2", "1.3"}:
        blockers.append("public_tls: minimum_tls_version must be 1.2 or 1.3")
    required_checks = set(evidence.protected_workflow.required_checks)
    if not {"gitleaks", "package", "test"}.issubset(required_checks):
        blockers.append(
            "protected_workflow: required_checks must include gitleaks, package, and test"
        )
    return blockers


def evaluate_production_readiness(
    payload: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        evidence = ProductionReadinessEvidence.model_validate(payload)
    except ValidationError as error:
        blockers = _validation_blockers(error)
        return {
            "schema_version": "1",
            "ready": False,
            "manifest_digest": None,
            "blocker_count": len(blockers),
            "blockers": blockers,
        }

    evaluated_at = now or datetime.now(UTC)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    blockers = sorted(
        _freshness_blockers(evidence, now=evaluated_at)
        + _binding_blockers(evidence)
        + _outcome_blockers(evidence)
    )
    return {
        "schema_version": "1",
        "ready": not blockers,
        "manifest_digest": _canonical_digest(evidence),
        "blocker_count": len(blockers),
        "blockers": blockers,
    }


def evaluate_production_readiness_file(
    path: Path,
    *,
    maximum_bytes: int = 65_536,
) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > maximum_bytes:
        raise ValueError("production readiness manifest exceeds the size limit")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("production readiness manifest is invalid JSON") from error
    return evaluate_production_readiness(payload)

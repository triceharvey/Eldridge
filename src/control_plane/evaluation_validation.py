from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

from control_plane.domain import ProviderResult, TaskKind, ValidationError
from control_plane.validation import validate_provider_result


@dataclass(frozen=True)
class EvaluationArtifact:
    artifact_id: str
    output: dict[str, Any]
    output_digest: str
    task_kind: TaskKind
    workflow_version: int
    candidate_revision: str | None


@dataclass(frozen=True)
class ValidationOutcome:
    passed: bool
    details: dict[str, Any]


class EvaluationValidator(Protocol):
    name: str
    version: str

    def validate(self, artifact: EvaluationArtifact) -> ValidationOutcome: ...


class SchemaValidator:
    name = "schema"
    version = "evaluation-schema/v1"

    def validate(self, artifact: EvaluationArtifact) -> ValidationOutcome:
        canonical = json.dumps(artifact.output, sort_keys=True, separators=(",", ":")).encode()
        observed_digest = "sha256:" + sha256(canonical).hexdigest()
        if observed_digest != artifact.output_digest:
            return ValidationOutcome(False, {"reason": "output_digest_mismatch"})
        try:
            validate_provider_result(
                artifact.task_kind,
                ProviderResult(
                    status="SUCCEEDED",
                    output=artifact.output,
                    provider="trusted-validator",
                    model="deterministic",
                    usage={},
                ),
            )
        except ValidationError:
            return ValidationOutcome(False, {"reason": "task_schema_invalid"})
        return ValidationOutcome(
            True,
            {
                "reason": "task_schema_valid",
                "canonical_size_bytes": len(canonical),
            },
        )


class SecurityBoundaryValidator:
    name = "security"
    version = "evaluation-security-boundary/v1"
    _forbidden_keys = frozenset(
        {
            "api_key",
            "authorization",
            "credential",
            "credentials",
            "password",
            "private_key",
            "secret",
            "token",
        }
    )
    _authority_keys = frozenset(
        {
            "approval_granted",
            "deploy_authorized",
            "merge_authorized",
            "publication_authorized",
        }
    )

    def validate(self, artifact: EvaluationArtifact) -> ValidationOutcome:
        observed_keys = self._keys(artifact.output)
        forbidden = sorted(observed_keys & self._forbidden_keys)
        authority = sorted(observed_keys & self._authority_keys)
        passed = not forbidden and not authority
        return ValidationOutcome(
            passed,
            {
                "reason": "boundary_clean" if passed else "forbidden_output_fields",
                "forbidden_fields": forbidden,
                "authority_fields": authority,
            },
        )

    @classmethod
    def _keys(cls, value: object) -> set[str]:
        if isinstance(value, dict):
            keys = {str(key).casefold() for key in value}
            for item in value.values():
                keys.update(cls._keys(item))
            return keys
        if isinstance(value, list):
            list_keys: set[str] = set()
            for item in value:
                list_keys.update(cls._keys(item))
            return list_keys
        return set()


class RevisionBindingValidator:
    name = "revision_binding"
    version = "evaluation-revision-binding/v1"

    def validate(self, artifact: EvaluationArtifact) -> ValidationOutcome:
        passed = artifact.workflow_version > 0 and (
            artifact.candidate_revision is None
            or (
                bool(artifact.candidate_revision.strip())
                and len(artifact.candidate_revision) <= 128
            )
        )
        return ValidationOutcome(
            passed,
            {
                "reason": "snapshot_bound" if passed else "snapshot_binding_invalid",
                "workflow_version": artifact.workflow_version,
                "candidate_revision_present": artifact.candidate_revision is not None,
            },
        )


def default_evaluation_validators() -> tuple[EvaluationValidator, ...]:
    return (SchemaValidator(), SecurityBoundaryValidator(), RevisionBindingValidator())

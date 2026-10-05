"""Bind a proposed visual critique to verified local image evidence.

The caller supplies visual observations. This module verifies image identity and
the review contract; it cannot independently determine whether an observation
is visually correct or whether the named reviewer actually inspected the image.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from control_plane.visual_evidence import VisualEvidenceManifest, inspect_visual_evidence


class VisualFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal[
        "PERSPECTIVE",
        "SCALE",
        "OCCLUSION",
        "ANATOMY",
        "CONTINUITY",
        "PHYSICAL_INTEGRATION",
        "LIGHTING",
        "OTHER",
    ]
    severity: Literal["BLOCKER", "MAJOR", "MINOR"]
    source: Literal["CREATOR_FEEDBACK", "REVIEWER_VISUAL_OBSERVATION"]
    observation: str = Field(min_length=10, max_length=2000)
    location: str = Field(min_length=1, max_length=240)
    retry_direction: str = Field(min_length=10, max_length=2000)


class VisualReviewAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    repository_scope: str = Field(min_length=1, max_length=200)
    base_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    asset_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    shot_id: str = Field(min_length=1, max_length=100)
    reviewer_id: str = Field(min_length=1, max_length=100)
    findings: tuple[VisualFinding, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_actionable_blocker(self) -> VisualReviewAssessment:
        if not any(item.severity == "BLOCKER" for item in self.findings):
            raise ValueError("visual retry assessment requires a blocking finding")
        return self

    @classmethod
    def from_file(cls, path: Path) -> VisualReviewAssessment:
        candidate = path.resolve()
        if not candidate.is_file() or candidate.stat().st_size > 65_536:
            raise ValueError("visual review assessment is missing or too large")
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("visual review assessment is invalid") from exc
        return cls.model_validate(payload)


def inspect_visual_review(
    assessment: VisualReviewAssessment,
    *,
    evidence_manifest: VisualEvidenceManifest,
    repository_registry_file: Path,
) -> dict[str, Any]:
    """Return a fail-closed retry packet without model, browser, or approval actions."""

    if (
        assessment.repository_scope != evidence_manifest.repository_scope
        or assessment.base_revision != evidence_manifest.base_revision
    ):
        raise ValueError("visual review does not match the evidence repository revision")
    evidence = inspect_visual_evidence(
        evidence_manifest, repository_registry_file=repository_registry_file
    )
    matches = [asset for asset in evidence["assets"] if asset["asset_id"] == assessment.asset_id]
    if len(matches) != 1 or matches[0]["sha256"] != assessment.asset_sha256:
        raise ValueError("visual review asset ID or digest does not match verified evidence")
    asset = matches[0]
    if not asset["declared_status"].startswith("REJECTED_"):
        raise ValueError("visual retry assessment requires a rejected candidate")

    return {
        "schema_version": "1",
        "result": "RETRY_PROPOSED_NOT_EXECUTED",
        "repository_scope": assessment.repository_scope,
        "base_revision": assessment.base_revision,
        "shot_id": assessment.shot_id,
        "asset_id": assessment.asset_id,
        "asset_sha256": asset["sha256"],
        "asset_path": asset["local_path"],
        "declared_status": asset["declared_status"],
        "image_identity_verified": True,
        "visual_findings_independently_verified": False,
        "reviewer_identity_verified": False,
        "reviewer_id_claim": assessment.reviewer_id,
        "findings": [item.model_dump() for item in assessment.findings],
        "retry_directions": [item.retry_direction for item in assessment.findings],
        "creator_approval_recorded": False,
        "runway_action_taken": False,
        "provider_egress": False,
    }

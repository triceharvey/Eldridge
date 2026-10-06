"""Read-only binding of a local video candidate and limited reviewer evidence.

Frame derivation and local capture integrity are optional, explicit checks. Neither
authenticates a provider job, reviewer identity, visual truth, or creator approval.
"""

from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from control_plane.tools import validate_relative_path
from control_plane.video_frames import verify_frame_derivation
from control_plane.visual_evidence import VisualEvidenceManifest, inspect_visual_evidence
from control_plane.workspaces import RepositoryRegistry

MAX_VIDEO_BYTES = 256 * 1024 * 1024
MAX_FRAME_BYTES = 16 * 1024 * 1024


class VideoFrameSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp_ms: int = Field(ge=0, le=60_000)
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class VideoCandidateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    repository_scope: str = Field(min_length=1, max_length=200)
    source_base_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    source_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    video_asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    video_path: str = Field(min_length=1, max_length=500)
    video_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_artifact_id_claim: str = Field(min_length=1, max_length=200)
    duration_ms_claim: int = Field(ge=1000, le=60_000)
    credits_charged_claim: int = Field(ge=0, le=100_000)
    frames: tuple[VideoFrameSample, ...] = Field(min_length=3, max_length=24)

    @model_validator(mode="after")
    def validate_samples(self) -> VideoCandidateManifest:
        timestamps = [frame.timestamp_ms for frame in self.frames]
        if timestamps != sorted(set(timestamps)) or timestamps[-1] >= self.duration_ms_claim:
            raise ValueError("video frame timestamps must be unique, ordered, and inside duration")
        if (
            timestamps[0] > 250
            or timestamps[-1] < self.duration_ms_claim - 500
            or not any(
                self.duration_ms_claim // 5 <= time <= self.duration_ms_claim * 4 // 5
                for time in timestamps[1:-1]
            )
        ):
            raise ValueError("video samples must cover opening, middle, and ending")
        paths = [frame.path for frame in self.frames]
        if len(paths) != len(set(paths)) or self.video_path in paths:
            raise ValueError("video candidate paths must be distinct")
        for path in [self.video_path, *paths]:
            validate_relative_path(path)
        return self

    @classmethod
    def from_file(cls, path: Path) -> VideoCandidateManifest:
        return cls.model_validate(_read_json(path, "video candidate manifest"))


class RunwayCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "JOB_DETAILS",
        "GENERATION_SETTINGS",
        "CREDIT_LEDGER",
        "CREDIT_BALANCE_BEFORE",
        "DOWNLOAD_RECORD",
    ]
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RunwayJobEvidence(BaseModel):
    """Browser-observed receipt; never a provider-signed attestation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    capture_method: Literal["BROWSER_OBSERVED"] = "BROWSER_OBSERVED"
    provider_artifact_id_claim: str = Field(min_length=1, max_length=200)
    video_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_name_claim: str = Field(min_length=1, max_length=100)
    duration_ms_claim: int = Field(ge=1000, le=60_000)
    credits_charged_claim: int = Field(ge=0, le=100_000)
    captures: tuple[RunwayCapture, ...] = Field(min_length=3, max_length=5)

    @model_validator(mode="after")
    def validate_capture_paths(self) -> RunwayJobEvidence:
        paths = [capture.path for capture in self.captures]
        if len(paths) != len(set(paths)):
            raise ValueError("Runway capture paths must be distinct")
        kinds = [capture.kind for capture in self.captures]
        if len(kinds) != len(set(kinds)) or not {
            "JOB_DETAILS",
            "GENERATION_SETTINGS",
            "CREDIT_LEDGER",
        }.issubset(kinds):
            raise ValueError("Runway receipt requires distinct job, settings, and credit captures")
        for path in paths:
            validate_relative_path(path)
        return self

    @classmethod
    def from_file(cls, path: Path) -> RunwayJobEvidence:
        return cls.model_validate(_read_json(path, "Runway job evidence"))


class VideoReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal[
        "IDENTITY",
        "ANATOMY",
        "PERSPECTIVE",
        "SCALE",
        "OCCLUSION",
        "CONTINUITY",
        "REFLECTION",
        "MOTION",
        "LIGHTING",
        "OTHER",
    ]
    severity: Literal["BLOCKER", "MAJOR", "MINOR"]
    location: str = Field(min_length=1, max_length=240)
    observation: str = Field(min_length=10, max_length=2000)


class VideoReviewerAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    video_asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    video_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    producer_model_family: str = Field(min_length=1, max_length=100)
    reviewer_model_family: str = Field(min_length=1, max_length=100)
    reviewer_id_claim: str = Field(min_length=1, max_length=100)
    frames_reviewed_sha256: tuple[str, ...] = Field(min_length=3, max_length=24)
    recommendation: Literal["REJECT", "HOLD_FOR_CREATOR"]
    findings: tuple[VideoReviewFinding, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_distinct_reviewer_and_reasoned_rejection(self) -> VideoReviewerAssessment:
        if self.producer_model_family.casefold() == self.reviewer_model_family.casefold():
            raise ValueError("video reviewer must claim a distinct model family")
        if self.recommendation == "REJECT" and not any(
            item.severity == "BLOCKER" for item in self.findings
        ):
            raise ValueError("video rejection requires a blocking finding")
        return self

    @classmethod
    def from_file(cls, path: Path) -> VideoReviewerAssessment:
        return cls.model_validate(_read_json(path, "video reviewer assessment"))


def _read_json(path: Path, label: str) -> Any:
    candidate = path.resolve()
    if not candidate.is_file() or candidate.stat().st_size > 65_536:
        raise ValueError(f"{label} is missing or too large")
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid") from exc


def _verified_local_file(root: Path, relative_path: str, expected_digest: str, limit: int) -> Path:
    relative = validate_relative_path(relative_path)
    candidate = root.joinpath(*relative.parts)
    path = candidate.resolve(strict=True)
    if not path.is_relative_to(root) or candidate.is_symlink() or not path.is_file():
        raise ValueError("video evidence path is not a regular registered file")
    size = path.stat().st_size
    if size <= 0 or size > limit:
        raise ValueError("video evidence file exceeds the size boundary")
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if f"sha256:{digest.hexdigest()}" != expected_digest:
        raise ValueError("video evidence digest does not match local bytes")
    return path


def _capture_from_file(root: Path, kind: str, relative_path: str) -> RunwayCapture:
    relative = validate_relative_path(relative_path)
    candidate = root.joinpath(*relative.parts)
    path = candidate.resolve(strict=True)
    if not path.is_relative_to(root) or candidate.is_symlink() or not path.is_file():
        raise ValueError("Runway capture path is not a regular registered file")
    if not 0 < path.stat().st_size <= MAX_FRAME_BYTES:
        raise ValueError("Runway capture exceeds the size boundary")
    with path.open("rb") as stream:
        signature = stream.read(8)
    if not (signature.startswith(b"\x89PNG\r\n\x1a\n") or signature.startswith(b"\xff\xd8\xff")):
        raise ValueError("Runway capture must be a PNG or JPEG image")
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return RunwayCapture(kind=kind, path=relative_path, sha256=f"sha256:{digest.hexdigest()}")


def create_runway_job_evidence(
    manifest: VideoCandidateManifest,
    *,
    repository_registry_file: Path,
    model_name_claim: str,
    capture_paths: dict[str, str],
    output: Path,
) -> RunwayJobEvidence:
    """Hash already-saved browser captures and atomically create a receipt; no egress."""

    registry = RepositoryRegistry.from_file(repository_registry_file)
    root = registry.resolve(manifest.repository_scope).path
    _verified_local_file(root, manifest.video_path, manifest.video_sha256, MAX_VIDEO_BYTES)
    required = {"JOB_DETAILS", "GENERATION_SETTINGS", "CREDIT_LEDGER"}
    optional = {"CREDIT_BALANCE_BEFORE", "DOWNLOAD_RECORD"}
    if not required.issubset(capture_paths) or not set(capture_paths).issubset(required | optional):
        raise ValueError("capture package requires job, settings, and credit records")
    reserved = {manifest.video_path, *(frame.path for frame in manifest.frames)}
    if set(capture_paths.values()) & reserved:
        raise ValueError("Runway captures must be distinct from video and sampled frames")
    captures = tuple(
        _capture_from_file(root, kind, relative_path)
        for kind, relative_path in capture_paths.items()
    )
    receipt = RunwayJobEvidence(
        provider_artifact_id_claim=manifest.provider_artifact_id_claim,
        video_sha256=manifest.video_sha256,
        model_name_claim=model_name_claim,
        duration_ms_claim=manifest.duration_ms_claim,
        credits_charged_claim=manifest.credits_charged_claim,
        captures=captures,
    )
    parent = output.parent.resolve(strict=True)
    if not parent.is_relative_to(root) or output.exists() or output.is_symlink():
        raise ValueError("receipt output must be a new file in the registered repository")
    payload = (json.dumps(receipt.model_dump(), indent=2) + "\n").encode("utf-8")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".runway-receipt-", dir=parent, delete=False
        ) as temp:
            temporary_path = Path(temp.name)
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
        os.link(temporary_path, output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return receipt


def inspect_video_candidate(
    manifest: VideoCandidateManifest,
    assessment: VideoReviewerAssessment,
    *,
    source_evidence_manifest: VisualEvidenceManifest,
    repository_registry_file: Path,
    verify_frames: bool = False,
    runway_job_evidence: RunwayJobEvidence | None = None,
) -> dict[str, Any]:
    """Check source and candidate bytes, then bind limited reviewer claims."""

    if (
        manifest.repository_scope != source_evidence_manifest.repository_scope
        or manifest.source_base_revision != source_evidence_manifest.base_revision
    ):
        raise ValueError("video source does not match registered image evidence")
    source = inspect_visual_evidence(
        source_evidence_manifest, repository_registry_file=repository_registry_file
    )
    matches = [asset for asset in source["assets"] if asset["asset_id"] == manifest.source_asset_id]
    if len(matches) != 1 or matches[0]["sha256"] != manifest.source_sha256:
        raise ValueError("video source asset or digest does not match verified image")
    if matches[0]["declared_status"] != "APPROVED_PRODUCTION_PLATE":
        raise ValueError("video source requires declared approved production plate status")

    registry = RepositoryRegistry.from_file(repository_registry_file)
    root = registry.resolve(manifest.repository_scope).path
    video = _verified_local_file(root, manifest.video_path, manifest.video_sha256, MAX_VIDEO_BYTES)
    with video.open("rb") as stream:
        header = stream.read(12)
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise ValueError("video candidate is not an MP4-family file")
    frames = []
    for frame in manifest.frames:
        path = _verified_local_file(root, frame.path, frame.sha256, MAX_FRAME_BYTES)
        with path.open("rb") as stream:
            signature = stream.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise ValueError("video frame must be a PNG for reproducible extraction")
        frames.append(
            {"timestamp_ms": frame.timestamp_ms, "path": str(path), "sha256": frame.sha256}
        )

    if (
        assessment.video_asset_id != manifest.video_asset_id
        or assessment.video_sha256 != manifest.video_sha256
    ):
        raise ValueError("video reviewer assessment does not match candidate identity")
    if list(assessment.frames_reviewed_sha256) != [frame.sha256 for frame in manifest.frames]:
        raise ValueError("video reviewer frame claims do not match verified samples")
    derivation = verify_frame_derivation(video, manifest.frames) if verify_frames else None
    capture_report = None
    if runway_job_evidence is not None:
        if (
            runway_job_evidence.provider_artifact_id_claim != manifest.provider_artifact_id_claim
            or runway_job_evidence.video_sha256 != manifest.video_sha256
            or runway_job_evidence.duration_ms_claim != manifest.duration_ms_claim
            or runway_job_evidence.credits_charged_claim != manifest.credits_charged_claim
        ):
            raise ValueError("Runway job receipt claims do not match video candidate")
        captures = []
        for capture in runway_job_evidence.captures:
            if capture.path == manifest.video_path or capture.path in {
                frame.path for frame in manifest.frames
            }:
                raise ValueError("Runway capture must be distinct from video and sampled frames")
            path = _verified_local_file(root, capture.path, capture.sha256, MAX_FRAME_BYTES)
            with path.open("rb") as stream:
                signature = stream.read(8)
            if not (
                signature.startswith(b"\x89PNG\r\n\x1a\n") or signature.startswith(b"\xff\xd8\xff")
            ):
                raise ValueError("Runway capture must be a PNG or JPEG image")
            captures.append({"kind": capture.kind, "path": str(path), "sha256": capture.sha256})
        capture_report = {
            "capture_method": runway_job_evidence.capture_method,
            "model_name_claim": runway_job_evidence.model_name_claim,
            "captures": captures,
            "local_capture_bytes_verified": True,
            "provider_origin_authenticated": False,
        }
    return {
        "schema_version": "1",
        "result": "VIDEO_REVIEW_CLAIMS_BOUND_TO_LOCAL_BYTES",
        "source_asset_id": manifest.source_asset_id,
        "source_sha256": manifest.source_sha256,
        "source_git_revision_verified": True,
        "source_approval_verified": False,
        "video_asset_id": manifest.video_asset_id,
        "video_path": str(video),
        "video_sha256": manifest.video_sha256,
        "video_bytes_verified": True,
        "video_git_revision_verified": False,
        "video_playback_verified": False,
        "provider_artifact_id_claim": manifest.provider_artifact_id_claim,
        "provider_identity_verified": False,
        "duration_ms_claim": manifest.duration_ms_claim,
        "credits_charged_claim": manifest.credits_charged_claim,
        "duration_and_cost_verified": False,
        "frames": frames,
        "frame_bytes_verified": True,
        "frame_derivation_from_video_verified": derivation is not None,
        "frame_extraction": derivation,
        "runway_job_evidence": capture_report,
        "reviewer_model_family_claim": assessment.reviewer_model_family,
        "reviewer_identity_verified": False,
        "reviewer_observations_independently_verified": False,
        "reviewer_recommendation": assessment.recommendation,
        "findings": [item.model_dump() for item in assessment.findings],
        "creator_decision_recorded": False,
        "provider_egress": False,
        "read_only": True,
    }

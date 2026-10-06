from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from control_plane.cli import main
from control_plane.video_trial import (
    RunwayJobEvidence,
    VideoCandidateManifest,
    VideoReviewerAssessment,
    create_runway_job_evidence,
    inspect_video_candidate,
)
from control_plane.visual_evidence import VisualEvidenceManifest

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0XcAAAAASUVORK5CYII="
)
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"


def _git(root: Path, *args: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    return subprocess.run(  # noqa: S603 - fixed local test executable and repository
        [executable, "-C", str(root), *args], capture_output=True, check=True, text=True
    ).stdout.strip()


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init")
    (root / "media").mkdir()
    (root / "media" / "source.png").write_bytes(PNG)
    _git(root, "add", "media/source.png")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "source",
    )
    revision = _git(root, "rev-parse", "HEAD")
    (root / "media" / "candidate.mp4").write_bytes(MP4)
    frames = []
    for timestamp_ms in (0, 500, 900):
        path = f"media/frame-{timestamp_ms}.png"
        (root / path).write_bytes(PNG)
        frames.append({"timestamp_ms": timestamp_ms, "path": path, "sha256": _digest(PNG)})

    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "scope_id": "example/project",
                    "path": str(root),
                    "base_revision": revision,
                    "writable_paths": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    source_manifest = tmp_path / "source.json"
    source_manifest.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "repository_scope": "example/project",
                "base_revision": revision,
                "assets": [
                    {
                        "asset_id": "SHOT-1",
                        "label": "Approved still",
                        "path": "media/source.png",
                        "sha256": _digest(PNG),
                        "declared_status": "APPROVED_PRODUCTION_PLATE",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "video.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "repository_scope": "example/project",
                "source_base_revision": revision,
                "source_asset_id": "SHOT-1",
                "source_sha256": _digest(PNG),
                "video_asset_id": "SHOT-1-MOTION-1",
                "video_path": "media/candidate.mp4",
                "video_sha256": _digest(MP4),
                "provider_artifact_id_claim": "provider-job-1",
                "duration_ms_claim": 1000,
                "credits_charged_claim": 25,
                "frames": frames,
            }
        ),
        encoding="utf-8",
    )
    assessment_path = tmp_path / "assessment.json"
    assessment_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "video_asset_id": "SHOT-1-MOTION-1",
                "video_sha256": _digest(MP4),
                "producer_model_family": "Runway",
                "reviewer_model_family": "Qwen",
                "reviewer_id_claim": "local-qwen-review",
                "frames_reviewed_sha256": [_digest(PNG)] * 3,
                "recommendation": "REJECT",
                "findings": [
                    {
                        "category": "ANATOMY",
                        "severity": "BLOCKER",
                        "location": "folded ear",
                        "observation": "The folded ear changes shape between the sampled frames.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return (
        root,
        registry,
        source_manifest,
        manifest_path,
        assessment_path,
        root / "media" / "candidate.mp4",
    )


def _digest(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _inspect(registry: Path, source: Path, manifest: Path, assessment: Path) -> dict:
    return inspect_video_candidate(
        VideoCandidateManifest.from_file(manifest),
        VideoReviewerAssessment.from_file(assessment),
        source_evidence_manifest=VisualEvidenceManifest.from_file(source),
        repository_registry_file=registry,
    )


def test_video_candidate_binds_bytes_but_not_visual_truth(tmp_path: Path) -> None:
    root, registry, source, manifest, assessment, _video = _inputs(tmp_path)
    report = _inspect(registry, source, manifest, assessment)
    assert report["source_git_revision_verified"] is True
    assert report["source_approval_verified"] is False
    assert report["video_bytes_verified"] is True
    assert report["video_playback_verified"] is False
    assert report["frame_bytes_verified"] is True
    assert report["frame_derivation_from_video_verified"] is False
    assert report["reviewer_identity_verified"] is False
    assert report["creator_decision_recorded"] is False
    assert report["provider_egress"] is False
    assert _git(root, "status", "--porcelain").count("??") == 4


def test_video_candidate_rejects_tampered_video_and_frame(tmp_path: Path) -> None:
    root, registry, source, manifest, assessment, video = _inputs(tmp_path)
    video.write_bytes(MP4 + b"tampered")
    with pytest.raises(ValueError, match="digest does not match"):
        _inspect(registry, source, manifest, assessment)
    video.write_bytes(MP4)
    (root / "media" / "frame-500.png").write_bytes(PNG + b"tampered")
    with pytest.raises(ValueError, match="digest does not match"):
        _inspect(registry, source, manifest, assessment)


def test_video_candidate_rejects_jpeg_sample_that_png_extractor_cannot_reproduce(
    tmp_path: Path,
) -> None:
    root, registry, source, manifest, assessment, _video = _inputs(tmp_path)
    jpeg_bytes = b"\xff\xd8\xff\xe0not-a-png"
    (root / "media" / "frame-500.png").write_bytes(jpeg_bytes)
    candidate = json.loads(manifest.read_text(encoding="utf-8"))
    candidate["frames"][1]["sha256"] = _digest(jpeg_bytes)
    manifest.write_text(json.dumps(candidate), encoding="utf-8")
    review = json.loads(assessment.read_text(encoding="utf-8"))
    review["frames_reviewed_sha256"][1] = _digest(jpeg_bytes)
    assessment.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="frame must be a PNG"):
        _inspect(registry, source, manifest, assessment)


def test_video_candidate_rejects_same_model_and_frame_claim_mismatch(tmp_path: Path) -> None:
    _root, registry, source, manifest, assessment, _video = _inputs(tmp_path)
    payload = json.loads(assessment.read_text(encoding="utf-8"))
    payload["reviewer_model_family"] = "runway"
    assessment.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="distinct model family"):
        VideoReviewerAssessment.from_file(assessment)
    payload["reviewer_model_family"] = "Qwen"
    payload["frames_reviewed_sha256"][1] = "sha256:" + "0" * 64
    assessment.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="frame claims"):
        _inspect(registry, source, manifest, assessment)


def test_video_candidate_rejects_bad_source_and_path(tmp_path: Path) -> None:
    _root, registry, source, manifest, assessment, _video = _inputs(tmp_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["assets"][0]["declared_status"] = "PENDING_CREATOR_REVIEW"
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="approved production plate"):
        _inspect(registry, source, manifest, assessment)
    payload["assets"][0]["declared_status"] = "APPROVED_PRODUCTION_PLATE"
    source.write_text(json.dumps(payload), encoding="utf-8")
    video_payload = json.loads(manifest.read_text(encoding="utf-8"))
    video_payload["video_path"] = "../outside.mp4"
    manifest.write_text(json.dumps(video_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="parent traversal"):
        VideoCandidateManifest.from_file(manifest)


def test_video_candidate_requires_timeline_coverage(tmp_path: Path) -> None:
    _root, _registry, _source, manifest, _assessment, _video = _inputs(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["frames"][1]["timestamp_ms"] = 250
    payload["frames"][2]["timestamp_ms"] = 400
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="opening, middle, and ending"):
        VideoCandidateManifest.from_file(manifest)


def test_cli_video_candidate_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, registry, source, manifest, assessment, _video = _inputs(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control-plane",
            "audit",
            "video-candidate",
            "--source-manifest",
            str(source),
            "--manifest",
            str(manifest),
            "--assessment",
            str(assessment),
            "--repository-registry",
            str(registry),
        ],
    )
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)["reviewer_recommendation"] == "REJECT"
    assert _git(root, "status", "--porcelain").count("??") == 4


def test_frame_derivation_opt_in_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, registry, source, manifest, assessment, _video = _inputs(tmp_path)
    called = []

    def verified(video: Path, samples: tuple) -> dict:
        called.append((video, samples))
        return {"backend": "test-decoder", "timestamps": []}

    monkeypatch.setattr("control_plane.video_trial.verify_frame_derivation", verified)
    report = inspect_video_candidate(
        VideoCandidateManifest.from_file(manifest),
        VideoReviewerAssessment.from_file(assessment),
        source_evidence_manifest=VisualEvidenceManifest.from_file(source),
        repository_registry_file=registry,
        verify_frames=True,
    )
    assert report["frame_derivation_from_video_verified"] is True
    assert report["frame_extraction"]["backend"] == "test-decoder"
    assert len(called) == 1

    def failed(_video: Path, _samples: tuple) -> dict:
        raise ValueError("extracted frame digest does not match archived sample")

    monkeypatch.setattr("control_plane.video_trial.verify_frame_derivation", failed)
    with pytest.raises(ValueError, match="digest does not match"):
        inspect_video_candidate(
            VideoCandidateManifest.from_file(manifest),
            VideoReviewerAssessment.from_file(assessment),
            source_evidence_manifest=VisualEvidenceManifest.from_file(source),
            repository_registry_file=registry,
            verify_frames=True,
        )


def test_runway_receipt_binds_capture_bytes_without_authenticating_provider(tmp_path: Path) -> None:
    root, registry, source, manifest, assessment, _video = _inputs(tmp_path)
    capture = root / "media" / "runway-job.png"
    capture.write_bytes(PNG)
    (root / "media" / "runway-settings.png").write_bytes(PNG)
    (root / "media" / "runway-credits.png").write_bytes(PNG)
    payload = {
        "provider_artifact_id_claim": "provider-job-1",
        "video_sha256": _digest(MP4),
        "model_name_claim": "Gen-4 Turbo",
        "duration_ms_claim": 1000,
        "credits_charged_claim": 25,
        "captures": [
            {
                "kind": "JOB_DETAILS",
                "path": "media/runway-job.png",
                "sha256": _digest(capture.read_bytes()),
            },
            {
                "kind": "GENERATION_SETTINGS",
                "path": "media/runway-settings.png",
                "sha256": _digest(PNG),
            },
            {"kind": "CREDIT_LEDGER", "path": "media/runway-credits.png", "sha256": _digest(PNG)},
        ],
    }
    receipt = RunwayJobEvidence.model_validate(payload)

    def inspect() -> dict:
        return inspect_video_candidate(
            VideoCandidateManifest.from_file(manifest),
            VideoReviewerAssessment.from_file(assessment),
            source_evidence_manifest=VisualEvidenceManifest.from_file(source),
            repository_registry_file=registry,
            runway_job_evidence=receipt,
        )

    report = inspect()
    assert report["runway_job_evidence"]["local_capture_bytes_verified"] is True
    assert report["runway_job_evidence"]["provider_origin_authenticated"] is False
    assert report["provider_identity_verified"] is False
    capture.write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest does not match"):
        inspect()
    wrong_claim = RunwayJobEvidence.model_validate({**payload, "credits_charged_claim": 24})
    with pytest.raises(ValueError, match="do not match video candidate"):
        inspect_video_candidate(
            VideoCandidateManifest.from_file(manifest),
            VideoReviewerAssessment.from_file(assessment),
            source_evidence_manifest=VisualEvidenceManifest.from_file(source),
            repository_registry_file=registry,
            runway_job_evidence=wrong_claim,
        )


def test_runway_receipt_builder_requires_complete_captures_and_no_overwrite(tmp_path: Path) -> None:
    root, registry, _source, manifest_path, _assessment, _video = _inputs(tmp_path)
    manifest = VideoCandidateManifest.from_file(manifest_path)
    paths = {
        "JOB_DETAILS": "media/job.png",
        "GENERATION_SETTINGS": "media/settings.png",
        "CREDIT_LEDGER": "media/credits.png",
    }
    for path in paths.values():
        (root / path).write_bytes(PNG)
    output = root / "media" / "receipt.json"
    with pytest.raises(ValueError, match="requires job, settings, and credit"):
        create_runway_job_evidence(
            manifest,
            repository_registry_file=registry,
            model_name_claim="Gen-4 Turbo",
            capture_paths={"JOB_DETAILS": paths["JOB_DETAILS"]},
            output=output,
        )
    receipt = create_runway_job_evidence(
        manifest,
        repository_registry_file=registry,
        model_name_claim="Gen-4 Turbo",
        capture_paths=paths,
        output=output,
    )
    assert RunwayJobEvidence.from_file(output) == receipt
    assert [item.kind for item in receipt.captures] == list(paths)
    with pytest.raises(ValueError, match="must be a new file"):
        create_runway_job_evidence(
            manifest,
            repository_registry_file=registry,
            model_name_claim="Gen-4 Turbo",
            capture_paths=paths,
            output=output,
        )
    assert json.loads(output.read_text(encoding="utf-8"))["credits_charged_claim"] == 25


def test_runway_receipt_rejects_missing_kind_and_non_image(tmp_path: Path) -> None:
    root, registry, _source, manifest_path, _assessment, _video = _inputs(tmp_path)
    manifest = VideoCandidateManifest.from_file(manifest_path)
    with pytest.raises(ValueError, match="requires distinct job, settings, and credit"):
        RunwayJobEvidence.model_validate(
            {
                "provider_artifact_id_claim": "provider-job-1",
                "video_sha256": _digest(MP4),
                "model_name_claim": "Gen-4 Turbo",
                "duration_ms_claim": 1000,
                "credits_charged_claim": 25,
                "captures": [
                    {"kind": "JOB_DETAILS", "path": f"media/{i}.png", "sha256": _digest(PNG)}
                    for i in range(3)
                ],
            }
        )
    (root / "media" / "job.png").write_text("not an image", encoding="utf-8")
    for name in ("settings.png", "credits.png"):
        (root / "media" / name).write_bytes(PNG)
    with pytest.raises(ValueError, match="PNG or JPEG"):
        create_runway_job_evidence(
            manifest,
            repository_registry_file=registry,
            model_name_claim="Gen-4 Turbo",
            capture_paths={
                "JOB_DETAILS": "media/job.png",
                "GENERATION_SETTINGS": "media/settings.png",
                "CREDIT_LEDGER": "media/credits.png",
            },
            output=root / "media" / "receipt.json",
        )


def test_cli_creates_runway_receipt_from_local_capture_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, registry, _source, manifest, _assessment, _video = _inputs(tmp_path)
    for name in ("job.png", "settings.png", "credits.png"):
        (root / "media" / name).write_bytes(PNG)
    output = root / "media" / "receipt.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control-plane",
            "capture",
            "runway-receipt",
            "--manifest",
            str(manifest),
            "--repository-registry",
            str(registry),
            "--model-name",
            "Gen-4 Turbo",
            "--job-details",
            "media/job.png",
            "--generation-settings",
            "media/settings.png",
            "--credit-ledger",
            "media/credits.png",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(capsys.readouterr().out)
    assert len(RunwayJobEvidence.from_file(output).captures) == 3

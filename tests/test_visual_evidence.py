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
from control_plane.visual_evidence import VisualEvidenceManifest, inspect_visual_evidence

ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0XcAAAAASUVORK5CYII="
)


def _git(repository: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    result = subprocess.run(  # noqa: S603 - local test repository and fixed Git command
        [executable, "-C", str(repository), *arguments],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _review_inputs(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init")
    image_path = repository / "review" / "candidate.png"
    image_path.parent.mkdir()
    image_path.write_bytes(ONE_PIXEL_PNG)
    _git(repository, "add", "review/candidate.png")
    _git(
        repository,
        "-c",
        "user.name=Test Operator",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Add review candidate",
    )
    revision = _git(repository, "rev-parse", "HEAD")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "scope_id": "example/project",
                    "path": str(repository),
                    "base_revision": revision,
                    "writable_paths": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "visual.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "repository_scope": "example/project",
                "base_revision": revision,
                "assets": [
                    {
                        "asset_id": "CANDIDATE-1",
                        "label": "Review candidate",
                        "path": "review/candidate.png",
                        "sha256": f"sha256:{sha256(ONE_PIXEL_PNG).hexdigest()}",
                        "declared_status": "PENDING_CREATOR_REVIEW",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return image_path, registry, manifest_path, revision


def test_visual_evidence_verifies_committed_image_without_approval(tmp_path: Path) -> None:
    image_path, registry, manifest_path, revision = _review_inputs(tmp_path)
    report = inspect_visual_evidence(
        VisualEvidenceManifest.from_file(manifest_path), repository_registry_file=registry
    )

    assert report["base_revision"] == revision
    assert report["read_only"] is True
    assert report["provider_egress"] is False
    assert report["assets"][0]["local_path"] == str(image_path)
    assert report["assets"][0]["mime_type"] == "image/png"
    assert report["assets"][0]["declared_status"] == "PENDING_CREATOR_REVIEW"
    assert report["assets"][0]["approval_recorded"] is False
    assert _git(image_path.parents[1], "status", "--porcelain") == ""


def test_visual_evidence_rejects_changed_working_image(tmp_path: Path) -> None:
    image_path, registry, manifest_path, _revision = _review_inputs(tmp_path)
    image_path.write_bytes(ONE_PIXEL_PNG + b"changed")
    with pytest.raises(ValueError, match="differs from committed image|do not match"):
        inspect_visual_evidence(
            VisualEvidenceManifest.from_file(manifest_path), repository_registry_file=registry
        )


def test_visual_evidence_rejects_wrong_digest(tmp_path: Path) -> None:
    _image_path, registry, manifest_path, _revision = _review_inputs(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["assets"][0]["sha256"] = "sha256:" + "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="digest or committed bytes do not match"):
        inspect_visual_evidence(
            VisualEvidenceManifest.from_file(manifest_path), repository_registry_file=registry
        )


def test_visual_evidence_rejects_untracked_and_traversal_paths(tmp_path: Path) -> None:
    image_path, registry, manifest_path, _revision = _review_inputs(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["assets"][0]["path"] = "../outside.png"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="parent traversal"):
        VisualEvidenceManifest.from_file(manifest_path)

    untracked = image_path.with_name("untracked.png")
    untracked.write_bytes(ONE_PIXEL_PNG)
    payload["assets"][0]["path"] = "review/untracked.png"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Git object is unavailable"):
        inspect_visual_evidence(
            VisualEvidenceManifest.from_file(manifest_path), repository_registry_file=registry
        )


def test_visual_evidence_rejects_stale_registry_base(tmp_path: Path) -> None:
    image_path, registry, manifest_path, _revision = _review_inputs(tmp_path)
    (image_path.parents[1] / "next.txt").write_text("next", encoding="utf-8")
    _git(image_path.parents[1], "add", "next.txt")
    _git(
        image_path.parents[1],
        "-c",
        "user.name=Test Operator",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Advance project",
    )
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload[0]["base_revision"] = _git(image_path.parents[1], "rev-parse", "HEAD")
    registry.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="base does not match"):
        inspect_visual_evidence(
            VisualEvidenceManifest.from_file(manifest_path), repository_registry_file=registry
        )


def test_cli_visual_evidence_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    image_path, registry, manifest_path, _revision = _review_inputs(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control-plane",
            "audit",
            "visual-evidence",
            "--manifest",
            str(manifest_path),
            "--repository-registry",
            str(registry),
        ],
    )
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["assets"][0]["local_path"] == str(image_path)
    assert _git(image_path.parents[1], "status", "--porcelain") == ""

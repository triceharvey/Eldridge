"""Read-only, revision-bound local image evidence for human review."""

from __future__ import annotations

import json
import re
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from control_plane.tools import validate_relative_path
from control_plane.workspaces import RepositoryRegistry, safe_git_environment

MAX_IMAGE_BYTES = 32 * 1024 * 1024
COMMIT_DIGEST = r"^[0-9a-f]{40}$"
SHA256_DIGEST = r"^sha256:[0-9a-f]{64}$"
GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40,64}$")


class VisualEvidenceAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    label: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=SHA256_DIGEST)
    declared_status: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        validate_relative_path(value)
        return value


class VisualEvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    repository_scope: str = Field(min_length=1, max_length=200)
    base_revision: str = Field(pattern=COMMIT_DIGEST)
    assets: tuple[VisualEvidenceAsset, ...] = Field(min_length=1, max_length=16)

    @classmethod
    def from_file(cls, manifest_path: Path) -> VisualEvidenceManifest:
        path = manifest_path.resolve()
        if not path.is_file() or path.stat().st_size > 32_768:
            raise ValueError("visual evidence manifest is missing or too large")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("visual evidence manifest is invalid") from exc
        return cls.model_validate(payload)


def _git(repository: Path, executable: str, *arguments: str) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable from registered Git boundary
            [executable, "-C", str(repository), *arguments],
            env=safe_git_environment(),
            capture_output=True,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("visual evidence Git check timed out") from exc
    if result.returncode != 0:
        raise ValueError("visual evidence Git object is unavailable")
    return result.stdout


def _mime_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("visual evidence format is not an allowed image")


def inspect_visual_evidence(
    manifest: VisualEvidenceManifest, *, repository_registry_file: Path
) -> dict[str, Any]:
    """Verify current local bytes against an exact committed image, with no writes or egress."""

    registry = RepositoryRegistry.from_file(repository_registry_file)
    registration = registry.resolve(manifest.repository_scope)
    resolved_base = registry.resolve_commit(manifest.repository_scope, manifest.base_revision)
    registered_base = registry.resolve_commit(manifest.repository_scope, registration.base_revision)
    if resolved_base != manifest.base_revision or resolved_base != registered_base:
        raise ValueError("visual evidence base does not match the registered immutable revision")

    assets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for asset in manifest.assets:
        if asset.asset_id in seen_ids:
            raise ValueError("visual evidence asset ID is duplicated")
        seen_ids.add(asset.asset_id)
        relative = validate_relative_path(asset.path)
        candidate = registration.path.joinpath(*relative.parts)
        path = candidate.resolve(strict=True)
        if (
            not path.is_relative_to(registration.path)
            or candidate.is_symlink()
            or not path.is_file()
        ):
            raise ValueError("visual evidence path is not a regular registered file")
        size = path.stat().st_size
        if size <= 0 or size > MAX_IMAGE_BYTES:
            raise ValueError("visual evidence image exceeds the size boundary")

        object_id = (
            _git(
                registration.path,
                registry.git,
                "rev-parse",
                "--verify",
                f"{resolved_base}:{asset.path}",
            )
            .decode("ascii")
            .strip()
        )
        if not GIT_OBJECT_ID.fullmatch(object_id):
            raise ValueError("visual evidence Git object ID is invalid")
        committed_size = int(
            _git(registration.path, registry.git, "cat-file", "-s", object_id).strip()
        )
        if committed_size != size or committed_size > MAX_IMAGE_BYTES:
            raise ValueError("visual evidence working file differs from committed image")
        committed_bytes = _git(registration.path, registry.git, "cat-file", "blob", object_id)
        local_bytes = path.read_bytes()
        digest = f"sha256:{sha256(local_bytes).hexdigest()}"
        if committed_bytes != local_bytes or digest != asset.sha256:
            raise ValueError("visual evidence digest or committed bytes do not match")
        mime_type = _mime_type(local_bytes)
        assets.append(
            {
                "asset_id": asset.asset_id,
                "label": asset.label,
                "declared_status": asset.declared_status,
                "local_path": str(path),
                "sha256": digest,
                "git_blob_id": object_id,
                "size_bytes": size,
                "mime_type": mime_type,
                "verified_against_commit": True,
                "approval_recorded": False,
            }
        )
    return {
        "schema_version": "1",
        "repository_scope": manifest.repository_scope,
        "base_revision": resolved_base,
        "read_only": True,
        "provider_egress": False,
        "assets": assets,
    }

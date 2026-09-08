from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from control_plane.domain import AuthorizationError, ValidationError

GITHUB_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
DELIVERY_ID = re.compile(r"^[0-9A-Fa-f-]{1,64}$")
TERMINAL_CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)


class GitHubApp(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str | None = Field(default=None, max_length=200)


class GitHubCheckRun(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    head_sha: str = Field(pattern=GITHUB_SHA.pattern)
    status: str = Field(min_length=1, max_length=32)
    conclusion: str | None = Field(default=None, max_length=32)
    details_url: str | None = Field(default=None, max_length=1000)
    app: GitHubApp | None = None


class GitHubRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str = Field(min_length=3, max_length=500, pattern=r"^[^/\s]+/[^/\s]+$")


class GitHubCheckRunEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = Field(min_length=1, max_length=32)
    check_run: GitHubCheckRun
    repository: GitHubRepository


@dataclass(frozen=True)
class NormalizedGitHubCheck:
    repository: str
    check_run_id: str
    check_name: str
    revision: str
    status: str
    conclusion: str
    details_url: str | None
    app_slug: str | None


def verify_github_signature(body: bytes, signature: str | None, secret: str) -> None:
    if not secret or signature is None or not signature.startswith("sha256="):
        raise AuthorizationError("GitHub webhook signature is missing or invalid")
    expected = "sha256=" + hmac.new(secret.encode(), body, sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AuthorizationError("GitHub webhook signature is missing or invalid")


def normalize_check_run(body: bytes, *, delivery_id: str) -> NormalizedGitHubCheck | None:
    if not DELIVERY_ID.fullmatch(delivery_id):
        raise ValidationError("GitHub delivery ID is invalid")
    try:
        event = GitHubCheckRunEvent.model_validate_json(body)
    except PydanticValidationError as exc:
        raise ValidationError("GitHub check_run payload is invalid") from exc
    if event.action != "completed":
        return None
    check = event.check_run
    if check.status != "completed" or check.conclusion not in TERMINAL_CONCLUSIONS:
        raise ValidationError("completed GitHub check_run lacks a terminal conclusion")
    return NormalizedGitHubCheck(
        repository=event.repository.full_name,
        check_run_id=str(check.id),
        check_name=check.name,
        revision=check.head_sha,
        status=check.status,
        conclusion=check.conclusion,
        details_url=check.details_url,
        app_slug=check.app.slug if check.app else None,
    )

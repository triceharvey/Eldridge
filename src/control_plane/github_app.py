from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from control_plane.domain import (
    IntegrationDisabledError,
    IntegrationResponseError,
    ValidationError,
)
from control_plane.github import GITHUB_SHA, TERMINAL_CONCLUSIONS
from control_plane.secrets import EnvironmentSecretResolver, SecretResolver

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
REQUESTED_PERMISSIONS = {
    "administration": "read",
    "checks": "read",
    "contents": "read",
    "pull_requests": "write",
}
ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


class GitHubRepositoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    full_name: str = Field(pattern=REPOSITORY_NAME.pattern)
    base_branch: str = Field(min_length=1, max_length=200)
    required_checks: tuple[str, ...] = Field(min_length=1, max_length=50)
    minimum_approving_reviews: int = Field(default=1, ge=1, le=10)
    require_admin_enforcement: bool = True
    require_linear_history: bool = True

    @model_validator(mode="after")
    def validate_repository_policy(self) -> GitHubRepositoryPolicy:
        _validate_branch(self.base_branch)
        if len(set(self.required_checks)) != len(self.required_checks):
            raise ValueError("required GitHub check names must be unique")
        if any(not item.strip() or len(item) > 200 for item in self.required_checks):
            raise ValueError("required GitHub check name is invalid")
        return self


class GitHubAppPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=64)
    enabled: bool = False
    api_base: str = GITHUB_API_BASE
    client_id: str = Field(min_length=1, max_length=200)
    installation_id: int = Field(gt=0)
    private_key_ref: str = Field(min_length=1, max_length=128)
    secret_environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    repositories: tuple[GitHubRepositoryPolicy, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_policy(self) -> GitHubAppPolicy:
        if self.api_base != GITHUB_API_BASE:
            raise ValueError("only the official GitHub API endpoint is permitted")
        if self.enabled and self.private_key_ref not in self.secret_environment:
            raise ValueError("enabled GitHub App lacks an allowlisted private-key reference")
        if any(
            not reference or not ENVIRONMENT_NAME.fullmatch(variable)
            for reference, variable in self.secret_environment.items()
        ):
            raise ValueError("GitHub secret environment mapping is invalid")
        repositories = [item.full_name.lower() for item in self.repositories]
        if len(repositories) != len(set(repositories)):
            raise ValueError("GitHub repository policies must be unique")
        return self

    @classmethod
    def from_file(cls, path: Path) -> GitHubAppPolicy:
        resolved = path.resolve()
        if not resolved.is_file() or resolved.stat().st_size > 65_536:
            raise ValueError("GitHub App policy file is missing or too large")
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("GitHub App policy file is invalid") from exc
        return cls.model_validate(payload)


@dataclass(frozen=True)
class PullRequestProposal:
    repository: str
    number: int
    url: str
    head_branch: str
    base_branch: str
    head_revision: str
    draft: bool


@dataclass(frozen=True)
class MergeReadiness:
    repository: str
    pull_number: int
    revision: str
    ready: bool
    reasons: tuple[str, ...]
    checks: dict[str, str]
    policy_version: str


@dataclass(frozen=True)
class MergeConfirmation:
    repository: str
    pull_number: int
    head_revision: str
    base_branch: str
    merge_commit_revision: str


class GitHubAppClient:
    """Least-privilege GitHub App boundary with no merge operation."""

    def __init__(
        self,
        policy: GitHubAppPolicy,
        secret_resolver: SecretResolver,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.policy = policy
        self.secret_resolver = secret_resolver
        self.client = client or httpx.Client(timeout=policy.timeout_seconds)

    def create_draft_pull_request(
        self,
        *,
        repository: str,
        head_branch: str,
        expected_revision: str,
        title: str,
        body: str,
    ) -> PullRequestProposal:
        repo_policy = self.validate_pull_request(
            repository=repository,
            head_branch=head_branch,
            expected_revision=expected_revision,
            title=title,
            body=body,
        )
        token = self._installation_token(repo_policy, pull_request_write=True)
        owner, repo = repository.split("/", 1)
        remote_ref = self._request_json(
            "GET",
            f"/repos/{quote(owner)}/{quote(repo)}/git/ref/heads/{quote(head_branch, safe='')}",
            token=token,
        )
        remote_revision = remote_ref.get("object", {}).get("sha")
        if remote_revision != expected_revision:
            raise IntegrationResponseError("GitHub branch head does not match candidate revision")
        response = self._request_json(
            "POST",
            f"/repos/{quote(owner)}/{quote(repo)}/pulls",
            token=token,
            json={
                "title": title.strip(),
                "body": body,
                "head": head_branch,
                "base": repo_policy.base_branch,
                "draft": True,
                "maintainer_can_modify": False,
            },
            expected_status=201,
        )
        proposal = self._normalize_pull(response, repository)
        if (
            proposal.head_revision != expected_revision
            or proposal.head_branch != head_branch
            or proposal.base_branch != repo_policy.base_branch
            or not proposal.draft
        ):
            raise IntegrationResponseError("created pull request does not match requested scope")
        return proposal

    def repository_policy(self, repository: str) -> GitHubRepositoryPolicy:
        return self._repository_policy(repository)

    def validate_pull_request(
        self,
        *,
        repository: str,
        head_branch: str,
        expected_revision: str,
        title: str,
        body: str,
    ) -> GitHubRepositoryPolicy:
        repo_policy = self._repository_policy(repository)
        _validate_branch(head_branch)
        _validate_revision(expected_revision)
        if not title.strip() or len(title) > 256 or len(body) > 20_000:
            raise ValidationError("pull-request title or body is invalid")
        return repo_policy

    def assess_merge_readiness(
        self, *, repository: str, pull_number: int, expected_revision: str
    ) -> MergeReadiness:
        repo_policy = self._repository_policy(repository)
        _validate_revision(expected_revision)
        if pull_number < 1:
            raise ValidationError("pull-request number must be positive")
        token = self._installation_token(repo_policy, pull_request_write=False)
        owner, repo = repository.split("/", 1)
        pull = self._request_json(
            "GET", f"/repos/{quote(owner)}/{quote(repo)}/pulls/{pull_number}", token=token
        )
        reasons: list[str] = []
        proposal = self._normalize_pull(pull, repository)
        if proposal.head_revision != expected_revision:
            reasons.append("pull_head_revision_mismatch")
        if proposal.base_branch != repo_policy.base_branch:
            reasons.append("pull_base_branch_mismatch")
        if pull.get("state") != "open":
            reasons.append("pull_request_not_open")
        if proposal.draft:
            reasons.append("pull_request_is_draft")

        try:
            protection = self._request_json(
                "GET",
                f"/repos/{quote(owner)}/{quote(repo)}/branches/"
                f"{quote(repo_policy.base_branch, safe='')}/protection",
                token=token,
            )
        except IntegrationResponseError:
            reasons.append("branch_protection_unverifiable")
        else:
            self._assess_protection(protection, repo_policy, reasons)
        checks_body = self._request_json(
            "GET",
            f"/repos/{quote(owner)}/{quote(repo)}/commits/{expected_revision}/check-runs",
            token=token,
            params={"filter": "latest", "per_page": "100"},
        )
        observed = self._assess_checks(checks_body, repo_policy, reasons)
        return MergeReadiness(
            repository=repository,
            pull_number=pull_number,
            revision=expected_revision,
            ready=not reasons,
            reasons=tuple(reasons),
            checks=observed,
            policy_version=self.policy.policy_version,
        )

    def find_pull_requests(
        self, *, repository: str, head_branch: str
    ) -> tuple[PullRequestProposal, ...]:
        repo_policy = self._repository_policy(repository)
        _validate_branch(head_branch)
        token = self._installation_token(repo_policy, pull_request_write=False)
        owner, repo = repository.split("/", 1)
        response = self._request(
            "GET",
            f"/repos/{quote(owner)}/{quote(repo)}/pulls",
            token=token,
            params={
                "state": "all",
                "head": f"{owner}:{head_branch}",
                "base": repo_policy.base_branch,
                "per_page": "100",
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise IntegrationResponseError("GitHub returned invalid JSON") from exc
        if not isinstance(payload, list):
            raise IntegrationResponseError("GitHub returned an invalid pull-request list")
        return tuple(self._normalize_pull(item, repository) for item in payload)

    def confirm_pull_request_merged(
        self, *, repository: str, pull_number: int, expected_revision: str
    ) -> MergeConfirmation:
        repo_policy = self._repository_policy(repository)
        _validate_revision(expected_revision)
        if pull_number < 1:
            raise ValidationError("pull-request number must be positive")
        token = self._installation_token(repo_policy, pull_request_write=False)
        owner, repo = repository.split("/", 1)
        path = f"/repos/{quote(owner)}/{quote(repo)}/pulls/{pull_number}"
        pull = self._request_json("GET", path, token=token)
        proposal = self._normalize_pull(pull, repository)
        merge_revision = pull.get("merge_commit_sha")
        if (
            proposal.head_revision != expected_revision
            or proposal.base_branch != repo_policy.base_branch
            or pull.get("state") != "closed"
            or pull.get("merged") is not True
            or not isinstance(merge_revision, str)
        ):
            raise IntegrationResponseError(
                "GitHub has not confirmed the expected pull-request merge"
            )
        _validate_revision(merge_revision)
        self._request("GET", f"{path}/merge", token=token, expected_status=204)
        return MergeConfirmation(
            repository=repository,
            pull_number=pull_number,
            head_revision=expected_revision,
            base_branch=proposal.base_branch,
            merge_commit_revision=merge_revision,
        )

    def _installation_token(
        self, repo_policy: GitHubRepositoryPolicy, *, pull_request_write: bool
    ) -> str:
        self._require_enabled()
        private_key = self.secret_resolver.resolve(self.policy.private_key_ref)
        now = int(time.time())
        try:
            app_jwt = jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": self.policy.client_id},
                private_key,
                algorithm="RS256",
            )
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise IntegrationResponseError("GitHub App private key is invalid") from exc
        requested_permissions = {
            **REQUESTED_PERMISSIONS,
            "pull_requests": "write" if pull_request_write else "read",
        }
        response = self._request_json(
            "POST",
            f"/app/installations/{self.policy.installation_id}/access_tokens",
            bearer=app_jwt,
            json={
                "repositories": [repo_policy.full_name.split("/", 1)[1]],
                "permissions": requested_permissions,
            },
            expected_status=201,
        )
        token = response.get("token")
        permissions = response.get("permissions")
        if not isinstance(token, str) or not token or not isinstance(permissions, dict):
            raise IntegrationResponseError("GitHub returned an invalid installation token")
        if any(value == "write" and name != "pull_requests" for name, value in permissions.items()):
            raise IntegrationResponseError("GitHub installation token exceeds allowed permissions")
        for name, level in requested_permissions.items():
            if permissions.get(name) != level:
                raise IntegrationResponseError(
                    "GitHub installation token lacks required permissions"
                )
        return token

    def _repository_policy(self, repository: str) -> GitHubRepositoryPolicy:
        self._require_enabled()
        for item in self.policy.repositories:
            if item.full_name.lower() == repository.lower():
                return item
        raise ValidationError("repository is not allowlisted for the GitHub App")

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise IntegrationDisabledError("GitHub App integration is disabled by policy")

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        bearer: str | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        credential = token or bearer
        if credential is None:
            raise IntegrationResponseError("GitHub request lacks an authorization credential")
        response = self._request(
            method,
            path,
            token=token,
            bearer=bearer,
            json=json,
            params=params,
            expected_status=expected_status,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise IntegrationResponseError("GitHub returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise IntegrationResponseError("GitHub returned an invalid response object")
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        bearer: str | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        expected_status: int = 200,
    ) -> httpx.Response:
        credential = token or bearer
        if credential is None:
            raise IntegrationResponseError("GitHub request lacks an authorization credential")
        try:
            response = self.client.request(
                method,
                f"{self.policy.api_base}{path}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {credential}",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                },
                json=json,
                params=params,
            )
        except httpx.RequestError as exc:
            raise IntegrationResponseError("GitHub request outcome is unknown") from exc
        if response.status_code != expected_status:
            raise IntegrationResponseError(
                f"GitHub request failed with HTTP {response.status_code}"
            )
        return response

    @staticmethod
    def _normalize_pull(payload: dict[str, Any], repository: str) -> PullRequestProposal:
        try:
            number = payload["number"]
            url = payload["html_url"]
            head_branch = payload["head"]["ref"]
            head_revision = payload["head"]["sha"]
            head_repository = payload["head"]["repo"]["full_name"]
            base_branch = payload["base"]["ref"]
            draft = payload["draft"]
        except (KeyError, TypeError) as exc:
            raise IntegrationResponseError("GitHub returned an invalid pull request") from exc
        if (
            not isinstance(number, int)
            or number < 1
            or not isinstance(url, str)
            or not isinstance(head_branch, str)
            or not isinstance(head_revision, str)
            or not isinstance(head_repository, str)
            or head_repository.lower() != repository.lower()
            or not isinstance(base_branch, str)
            or not isinstance(draft, bool)
        ):
            raise IntegrationResponseError("GitHub returned an invalid pull request")
        return PullRequestProposal(
            repository=repository,
            number=number,
            url=url,
            head_branch=head_branch,
            base_branch=base_branch,
            head_revision=head_revision,
            draft=draft,
        )

    @staticmethod
    def _assess_protection(
        payload: dict[str, Any], policy: GitHubRepositoryPolicy, reasons: list[str]
    ) -> None:
        contexts = payload.get("required_status_checks", {}).get("contexts", [])
        if not isinstance(contexts, list) or not set(policy.required_checks).issubset(contexts):
            reasons.append("required_checks_not_protected")
        if policy.require_admin_enforcement and not payload.get("enforce_admins", {}).get(
            "enabled"
        ):
            reasons.append("admin_enforcement_disabled")
        reviews = payload.get("required_pull_request_reviews", {})
        count = reviews.get("required_approving_review_count", 0)
        if not isinstance(count, int) or count < policy.minimum_approving_reviews:
            reasons.append("insufficient_required_reviews")
        if not reviews.get("dismiss_stale_reviews"):
            reasons.append("stale_review_dismissal_disabled")
        if not reviews.get("require_last_push_approval"):
            reasons.append("last_push_approval_disabled")
        if policy.require_linear_history and not payload.get("required_linear_history", {}).get(
            "enabled"
        ):
            reasons.append("linear_history_not_required")
        if payload.get("allow_force_pushes", {}).get("enabled"):
            reasons.append("force_pushes_allowed")
        if payload.get("allow_deletions", {}).get("enabled"):
            reasons.append("branch_deletions_allowed")

    @staticmethod
    def _assess_checks(
        payload: dict[str, Any], policy: GitHubRepositoryPolicy, reasons: list[str]
    ) -> dict[str, str]:
        runs = payload.get("check_runs")
        if not isinstance(runs, list):
            raise IntegrationResponseError("GitHub check-runs response is invalid")
        observed: dict[str, str] = {}
        for run in runs:
            if not isinstance(run, dict) or not isinstance(run.get("name"), str):
                continue
            name = run["name"]
            status = run.get("status")
            conclusion = run.get("conclusion")
            if status == "completed" and conclusion in TERMINAL_CONCLUSIONS:
                observed.setdefault(name, str(conclusion))
        for required in policy.required_checks:
            conclusion = observed.get(required)
            if conclusion is None:
                reasons.append(f"required_check_missing:{required}")
            elif conclusion != "success":
                reasons.append(f"required_check_not_successful:{required}")
        return {name: observed[name] for name in sorted(observed) if name in policy.required_checks}


def activate_github_app(
    policy: GitHubAppPolicy, *, secret_resolver: SecretResolver | None = None
) -> GitHubAppClient | None:
    if not policy.enabled:
        return None
    resolver = secret_resolver or EnvironmentSecretResolver(policy.secret_environment)
    resolver.resolve(policy.private_key_ref)
    return GitHubAppClient(policy, resolver)


def _validate_revision(revision: str) -> None:
    if not GITHUB_SHA.fullmatch(revision):
        raise ValidationError("GitHub revision must be an immutable commit digest")


def _validate_branch(branch: str) -> None:
    if (
        not BRANCH_NAME.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or "@{" in branch
        or branch.endswith(("/", ".", ".lock"))
    ):
        raise ValidationError("GitHub branch name is invalid")

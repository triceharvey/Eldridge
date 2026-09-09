import json
import secrets

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from control_plane.domain import IntegrationDisabledError, IntegrationResponseError
from control_plane.github_app import GitHubAppClient, GitHubAppPolicy
from control_plane.secrets import StaticSecretResolver

REVISION = "a" * 40
PRIVATE_KEY_REF = secrets.token_urlsafe(12)
INSTALLATION_TOKEN = secrets.token_urlsafe(32)


def _private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _policy(*, enabled: bool = True) -> GitHubAppPolicy:
    return GitHubAppPolicy.model_validate(
        {
            "policy_version": "github-app/test-v1",
            "enabled": enabled,
            "client_id": "Iv1.test-client",
            "installation_id": 123,
            "private_key_ref": PRIVATE_KEY_REF,
            "secret_environment": {PRIVATE_KEY_REF: "GITHUB_APP_PRIVATE_KEY"},
            "repositories": [
                {
                    "full_name": "owner/repository",
                    "base_branch": "main",
                    "required_checks": ["test", "security"],
                }
            ],
        }
    )


def _pull(*, draft: bool, revision: str = REVISION) -> dict[str, object]:
    return {
        "number": 42,
        "html_url": "https://github.com/owner/repository/pull/42",
        "state": "open",
        "draft": draft,
        "head": {
            "ref": "codex/task-1",
            "sha": revision,
            "repo": {"full_name": "owner/repository"},
        },
        "base": {"ref": "main"},
    }


def _protection() -> dict[str, object]:
    return {
        "required_status_checks": {"contexts": ["test", "security"]},
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {
            "required_approving_review_count": 1,
            "dismiss_stale_reviews": True,
            "require_last_push_approval": True,
        },
        "required_linear_history": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }


def _client(handler: object, *, enabled: bool = True) -> GitHubAppClient:
    return GitHubAppClient(
        _policy(enabled=enabled),
        StaticSecretResolver({PRIVATE_KEY_REF: _private_key()}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
    )


def test_create_draft_pull_request_verifies_head_and_cannot_request_merge_scope() -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, payload))
        if request.url.path.endswith("/access_tokens"):
            assert payload is not None
            app_token = request.headers["Authorization"].removeprefix("Bearer ")
            claims = jwt.decode(app_token, options={"verify_signature": False})
            assert claims["iss"] == "Iv1.test-client"
            assert claims["exp"] - claims["iat"] == 600
            assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
            permissions = payload["permissions"]
            assert payload["repositories"] == ["repository"]
            assert permissions == {
                "administration": "read",
                "checks": "read",
                "contents": "read",
                "pull_requests": "write",
            }
            return httpx.Response(
                201, json={"token": INSTALLATION_TOKEN, "permissions": permissions}
            )
        if "/git/ref/heads/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": REVISION}})
        assert payload is not None
        assert payload["draft"] is True
        assert payload["maintainer_can_modify"] is False
        return httpx.Response(201, json=_pull(draft=True))

    proposal = _client(handler).create_draft_pull_request(
        repository="owner/repository",
        head_branch="codex/task-1",
        expected_revision=REVISION,
        title="Proposed controlled change",
        body="Revision-bound evidence is attached in the control plane.",
    )

    assert proposal.number == 42
    assert proposal.draft is True
    assert [request[0] for request in requests] == ["POST", "GET", "POST"]
    assert not any("/merge" in request[1] for request in requests)


def test_readiness_rereads_pull_protection_and_checks_with_read_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            payload = json.loads(request.content)
            permissions = payload["permissions"]
            assert permissions["pull_requests"] == "read"
            assert permissions["contents"] == "read"
            return httpx.Response(
                201, json={"token": INSTALLATION_TOKEN, "permissions": permissions}
            )
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=_pull(draft=False))
        if request.url.path.endswith("/branches/main/protection"):
            return httpx.Response(200, json=_protection())
        return httpx.Response(
            200,
            json={
                "check_runs": [
                    {"name": "test", "status": "completed", "conclusion": "success"},
                    {"name": "security", "status": "completed", "conclusion": "success"},
                ]
            },
        )

    result = _client(handler).assess_merge_readiness(
        repository="owner/repository", pull_number=42, expected_revision=REVISION
    )

    assert result.ready is True
    assert result.reasons == ()
    assert result.checks == {"security": "success", "test": "success"}


def test_readiness_reports_every_failed_invariant_without_merging() -> None:
    observed_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_paths.append(request.url.path)
        if request.url.path.endswith("/access_tokens"):
            permissions = json.loads(request.content)["permissions"]
            return httpx.Response(
                201, json={"token": INSTALLATION_TOKEN, "permissions": permissions}
            )
        if request.url.path.endswith("/pulls/42"):
            payload = _pull(draft=True, revision="b" * 40)
            payload["state"] = "closed"
            return httpx.Response(200, json=payload)
        if request.url.path.endswith("/branches/main/protection"):
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={"check_runs": [{"name": "test", "status": "completed", "conclusion": "failure"}]},
        )

    result = _client(handler).assess_merge_readiness(
        repository="owner/repository", pull_number=42, expected_revision=REVISION
    )

    assert result.ready is False
    assert "pull_head_revision_mismatch" in result.reasons
    assert "pull_request_not_open" in result.reasons
    assert "pull_request_is_draft" in result.reasons
    assert "required_checks_not_protected" in result.reasons
    assert "required_check_not_successful:test" in result.reasons
    assert "required_check_missing:security" in result.reasons
    assert not any(path.endswith("/merge") for path in observed_paths)


def test_readiness_fails_closed_when_branch_protection_cannot_be_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            permissions = json.loads(request.content)["permissions"]
            return httpx.Response(
                201, json={"token": INSTALLATION_TOKEN, "permissions": permissions}
            )
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=_pull(draft=False))
        if request.url.path.endswith("/branches/main/protection"):
            return httpx.Response(403, json={"message": "feature unavailable"})
        return httpx.Response(
            200,
            json={
                "check_runs": [
                    {"name": "test", "status": "completed", "conclusion": "success"},
                    {"name": "security", "status": "completed", "conclusion": "success"},
                ]
            },
        )

    result = _client(handler).assess_merge_readiness(
        repository="owner/repository", pull_number=42, expected_revision=REVISION
    )

    assert result.ready is False
    assert result.reasons == ("branch_protection_unverifiable",)
    assert result.checks == {"security": "success", "test": "success"}


def test_merge_confirmation_is_read_only_and_revision_bound() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/access_tokens"):
            permissions = json.loads(request.content)["permissions"]
            assert permissions["pull_requests"] == "read"
            assert permissions["contents"] == "read"
            return httpx.Response(
                201, json={"token": INSTALLATION_TOKEN, "permissions": permissions}
            )
        if request.url.path.endswith("/pulls/42/merge"):
            return httpx.Response(204)
        payload = _pull(draft=False)
        payload["state"] = "closed"
        payload["merged"] = True
        payload["merge_commit_sha"] = "b" * 40
        return httpx.Response(200, json=payload)

    result = _client(handler).confirm_pull_request_merged(
        repository="owner/repository", pull_number=42, expected_revision=REVISION
    )

    assert result.merge_commit_revision == "b" * 40
    assert [method for method, _path in requests] == ["POST", "GET", "GET"]
    assert not any(method in {"PUT", "PATCH", "DELETE"} for method, _path in requests)


def test_disabled_client_and_overprivileged_token_fail_closed() -> None:
    with pytest.raises(IntegrationDisabledError, match="disabled"):
        _client(lambda _request: httpx.Response(500), enabled=False).create_draft_pull_request(
            repository="owner/repository",
            head_branch="codex/task-1",
            expected_revision=REVISION,
            title="Title",
            body="Body",
        )

    def handler(request: httpx.Request) -> httpx.Response:
        permissions = json.loads(request.content)["permissions"]
        permissions["contents"] = "write"
        return httpx.Response(201, json={"token": INSTALLATION_TOKEN, "permissions": permissions})

    with pytest.raises(IntegrationResponseError, match="exceeds allowed permissions"):
        _client(handler).create_draft_pull_request(
            repository="owner/repository",
            head_branch="codex/task-1",
            expected_revision=REVISION,
            title="Title",
            body="Body",
        )


def test_policy_rejects_unallowlisted_key_and_duplicate_repository() -> None:
    payload = _policy(enabled=False).model_dump(mode="json")
    payload["enabled"] = True
    payload["secret_environment"] = {}
    with pytest.raises(ValueError, match="allowlisted private-key"):
        GitHubAppPolicy.model_validate(payload)

    payload["secret_environment"] = {PRIVATE_KEY_REF: "GITHUB_APP_PRIVATE_KEY"}
    payload["repositories"] = [payload["repositories"][0], payload["repositories"][0]]
    with pytest.raises(ValueError, match="must be unique"):
        GitHubAppPolicy.model_validate(payload)

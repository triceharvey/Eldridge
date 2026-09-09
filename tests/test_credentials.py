import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from control_plane.credentials import (
    CredentialOperation,
    DenyCredentialBroker,
    FakeCredentialBroker,
    KubectlTokenRequester,
    KubernetesTokenRequestBroker,
    KubernetesTokenRequestBrokerFactory,
    WorkloadCredentialRequest,
)
from control_plane.domain import AuthorizationError, ValidationError


def _request(**overrides: object) -> WorkloadCredentialRequest:
    values: dict[str, object] = {
        "environment_id": "eldridge-local-k3d",
        "adapter_id": "opentofu-local-v1",
        "plan_digest": "a" * 64,
        "operation": CredentialOperation.APPLY_PLAN,
        "audience": "eldridge-local-k3d",
        "subject": "system:serviceaccount:eldridge:deployment-worker",
        "resource_scope": ("namespace/eldridge",),
        "lifetime_seconds": 300,
    }
    values.update(overrides)
    return WorkloadCredentialRequest(**values)  # type: ignore[arg-type]


def _broker() -> FakeCredentialBroker:
    return FakeCredentialBroker(
        environment_id="eldridge-local-k3d",
        adapter_id="opentofu-local-v1",
        audience="eldridge-local-k3d",
        subject="system:serviceaccount:eldridge:deployment-worker",
        resource_scope=frozenset({"namespace/eldridge"}),
        allowed_operations=frozenset({CredentialOperation.APPLY_PLAN, CredentialOperation.OBSERVE}),
        max_lifetime_seconds=300,
    )


def test_default_broker_fails_closed() -> None:
    with pytest.raises(AuthorizationError, match="disabled"):
        DenyCredentialBroker().issue(_request())


def test_fake_broker_returns_only_bound_expiring_metadata() -> None:
    now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    handle = _broker().issue(_request(), now=now)

    assert handle.simulated is True
    assert handle.reference.startswith("fake-credential:")
    assert handle.issued_at == now
    assert handle.expires_at == now + timedelta(seconds=300)
    assert handle.plan_digest == "a" * 64
    assert handle.resource_scope == ("namespace/eldridge",)
    assert not hasattr(handle, "token")
    assert not hasattr(handle, "secret")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"environment_id": "other"}, "environment"),
        ({"adapter_id": "other"}, "adapter"),
        ({"audience": "other"}, "trust binding"),
        ({"subject": "other"}, "trust binding"),
        ({"operation": CredentialOperation.ROLLBACK}, "operation"),
        ({"resource_scope": ("cluster/admin",)}, "resource scope"),
        ({"lifetime_seconds": 301}, "lifetime"),
    ],
)
def test_fake_broker_rejects_policy_widening(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(AuthorizationError, match=message):
        _broker().issue(_request(**overrides))


def test_fake_broker_rejects_invalid_digest_and_duplicate_scope() -> None:
    with pytest.raises(ValidationError, match="SHA-256"):
        _broker().issue(_request(plan_digest="not-a-digest"))
    with pytest.raises(ValidationError, match="non-empty and unique"):
        _broker().issue(_request(resource_scope=("namespace/eldridge", "namespace/eldridge")))


def test_fake_broker_has_a_hard_fifteen_minute_construction_limit() -> None:
    with pytest.raises(ValidationError, match="between 1 and 900"):
        FakeCredentialBroker(
            environment_id="eldridge-local-k3d",
            adapter_id="opentofu-local-v1",
            audience="eldridge-local-k3d",
            subject="deployment-worker",
            resource_scope=frozenset({"namespace/eldridge"}),
            allowed_operations=frozenset({CredentialOperation.APPLY_PLAN}),
            max_lifetime_seconds=901,
        )


class StubTokenRequester:
    def __init__(self, token: str = "", error: Exception | None = None) -> None:
        self.token = token
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create_token(
        self,
        *,
        namespace: str,
        service_account: str,
        audience: str,
        duration_seconds: int,
    ) -> str:
        self.calls.append(
            {
                "namespace": namespace,
                "service_account": service_account,
                "audience": audience,
                "duration_seconds": duration_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return self.token


def _jwt(claims: dict[str, object]) -> str:
    def encode(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{encode({'alg': 'RS256'})}.{encode(claims)}.signature"


def _kubernetes_request(**overrides: object) -> WorkloadCredentialRequest:
    values: dict[str, object] = {
        "environment_id": "eldridge-local-k3d",
        "adapter_id": "k3d-identity-observer-v1",
        "plan_digest": "b" * 64,
        "operation": CredentialOperation.OBSERVE,
        "audience": "eldridge-local-k3d",
        "subject": "system:serviceaccount:eldridge-validation:deployment-worker",
        "resource_scope": ("namespace/eldridge-validation",),
        "lifetime_seconds": 600,
    }
    values.update(overrides)
    return WorkloadCredentialRequest(**values)  # type: ignore[arg-type]


def _kubernetes_broker(
    requester: StubTokenRequester, *, enabled: bool = True
) -> KubernetesTokenRequestBroker:
    return KubernetesTokenRequestBroker(
        requester=requester,
        environment_id="eldridge-local-k3d",
        adapter_id="k3d-identity-observer-v1",
        plan_digest="b" * 64,
        namespace="eldridge-validation",
        service_account="deployment-worker",
        audience="eldridge-local-k3d",
        issuer="https://kubernetes.default.svc.cluster.local",
        resource_scope=("namespace/eldridge-validation",),
        operation=CredentialOperation.OBSERVE,
        enabled=enabled,
    )


def _valid_kubernetes_claims(issued: int) -> dict[str, object]:
    return {
        "aud": ["eldridge-local-k3d"],
        "iss": "https://kubernetes.default.svc.cluster.local",
        "sub": "system:serviceaccount:eldridge-validation:deployment-worker",
        "iat": issued,
        "exp": issued + 600,
        "kubernetes.io": {
            "namespace": "eldridge-validation",
            "serviceaccount": {"name": "deployment-worker", "uid": "test-uid"},
        },
    }


def test_kubernetes_broker_requires_explicit_opt_in_before_requesting_token() -> None:
    requester = StubTokenRequester()
    with pytest.raises(AuthorizationError, match="disabled"):
        _kubernetes_broker(requester, enabled=False).issue(_kubernetes_request())
    assert requester.calls == []


def test_kubernetes_broker_factory_creates_one_exact_request_binding() -> None:
    factory = KubernetesTokenRequestBrokerFactory(
        requester=StubTokenRequester(),
        environment_id="eldridge-local-k3d",
        adapter_id="k3d-identity-observer-v1",
        namespace="eldridge-validation",
        service_account="deployment-worker",
        audience="eldridge-local-k3d",
        issuer="https://kubernetes.default.svc.cluster.local",
        resource_scope=("namespace/eldridge-validation",),
        allowed_operations=frozenset({CredentialOperation.OBSERVE}),
        enabled=False,
    )

    request = _kubernetes_request(plan_digest="c" * 64)
    broker = factory.for_request(request)

    assert broker.plan_digest == "c" * 64
    assert broker.enabled is False
    with pytest.raises(ValidationError, match="SHA-256"):
        factory.for_request(_kubernetes_request(plan_digest="not-a-digest"))
    with pytest.raises(AuthorizationError, match="operation"):
        factory.for_request(_kubernetes_request(operation=CredentialOperation.ROLLBACK))


def test_kubernetes_broker_returns_only_validated_redacted_metadata() -> None:
    now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    issued = int(now.timestamp())
    raw_token = _jwt(_valid_kubernetes_claims(issued))
    requester = StubTokenRequester(raw_token)

    handle = _kubernetes_broker(requester).issue(_kubernetes_request(), now=now)

    assert requester.calls == [
        {
            "namespace": "eldridge-validation",
            "service_account": "deployment-worker",
            "audience": "eldridge-local-k3d",
            "duration_seconds": 600,
        }
    ]
    assert handle.simulated is False
    assert handle.broker_id == "kubernetes-token-request-v1"
    assert handle.reference.startswith("kubernetes-token:")
    assert raw_token not in repr(handle)
    assert not hasattr(handle, "token")
    assert not hasattr(handle, "secret")
    assert handle.expires_at - handle.issued_at == timedelta(seconds=600)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"environment_id": "other"}, "environment"),
        ({"adapter_id": "other"}, "adapter"),
        ({"audience": "other"}, "trust binding"),
        ({"subject": "other"}, "trust binding"),
        ({"operation": CredentialOperation.APPLY_PLAN}, "operation"),
        ({"resource_scope": ("namespace/other",)}, "resource scope"),
        ({"lifetime_seconds": 599}, "lifetime"),
        ({"plan_digest": "c" * 64}, "plan digest"),
    ],
)
def test_kubernetes_broker_rejects_every_policy_binding_before_issuance(
    overrides: dict[str, object], message: str
) -> None:
    requester = StubTokenRequester()
    with pytest.raises(AuthorizationError, match=message):
        _kubernetes_broker(requester).issue(_kubernetes_request(**overrides))
    assert requester.calls == []


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"aud": ["wrong"]},
        {"sub": "wrong"},
        {"iss": "https://wrong.invalid"},
        {"exp": 1_788_869_401},
        {"kubernetes.io": {"namespace": "wrong"}},
    ],
)
def test_kubernetes_broker_rejects_untrusted_returned_claims(
    claim_overrides: dict[str, object],
) -> None:
    issued = 1_788_868_800
    now = datetime.fromtimestamp(issued, UTC)
    claims = _valid_kubernetes_claims(issued)
    claims.update(claim_overrides)
    with pytest.raises(AuthorizationError):
        _kubernetes_broker(StubTokenRequester(_jwt(claims))).issue(_kubernetes_request(), now=now)


def test_kubernetes_broker_rejects_malformed_and_stale_credentials() -> None:
    with pytest.raises(ValidationError, match="malformed"):
        _kubernetes_broker(StubTokenRequester("not-a-jwt")).issue(_kubernetes_request())

    issued = 1_788_868_800
    claims = _valid_kubernetes_claims(issued)
    stale_time = datetime.fromtimestamp(issued + 61, UTC)
    with pytest.raises(AuthorizationError, match="validity window"):
        _kubernetes_broker(StubTokenRequester(_jwt(claims))).issue(
            _kubernetes_request(), now=stale_time
        )


def test_kubernetes_broker_sanitizes_unexpected_requester_failure() -> None:
    canary = "eyJ-secret-token-canary.signature"
    requester = StubTokenRequester(error=RuntimeError(canary))
    with pytest.raises(ValidationError) as caught:
        _kubernetes_broker(requester).issue(_kubernetes_request())
    assert canary not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_kubernetes_broker_blocks_consumer_result_and_error_leaks() -> None:
    now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    token = _jwt(_valid_kubernetes_claims(int(now.timestamp())))
    broker = _kubernetes_broker(StubTokenRequester(token))

    with pytest.raises(ValidationError, match="contained credential material"):
        broker.run(_kubernetes_request(), lambda credential, _handle: credential, now=now)

    def leaking_error(credential: str, _handle: object) -> None:
        raise ValidationError(f"unsafe {credential}")

    with pytest.raises(ValidationError, match="failed safely") as caught:
        broker.run(_kubernetes_request(), leaking_error, now=now)
    assert token not in str(caught.value)


def test_kubectl_requester_uses_typed_arguments_and_sanitizes_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kubeconfig = tmp_path / "kubeconfig"
    kubectl = tmp_path / "kubectl"
    kubeconfig.write_text("test")
    kubectl.write_text("#!/bin/sh\n")
    kubectl.chmod(0o700)
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return type(
            "Result",
            (),
            {"returncode": 1, "stdout": "", "stderr": "eyJ-secret-stderr.signature"},
        )()

    monkeypatch.setattr("control_plane.credentials.subprocess.run", fake_run)
    requester = KubectlTokenRequester(kubeconfig=kubeconfig, kubectl_path=kubectl)
    with pytest.raises(ValidationError, match="rejected") as caught:
        requester.create_token(
            namespace="eldridge-validation",
            service_account="deployment-worker",
            audience="eldridge-local-k3d",
            duration_seconds=600,
        )
    assert "secret-stderr" not in str(caught.value)
    assert captured["command"] == [
        str(kubectl.resolve()),
        "--kubeconfig",
        str(kubeconfig.resolve()),
        "create",
        "token",
        "deployment-worker",
        "--namespace",
        "eldridge-validation",
        "--audience",
        "eldridge-local-k3d",
        "--duration",
        "600s",
    ]

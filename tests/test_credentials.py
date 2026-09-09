from datetime import UTC, datetime, timedelta

import pytest

from control_plane.credentials import (
    CredentialOperation,
    DenyCredentialBroker,
    FakeCredentialBroker,
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

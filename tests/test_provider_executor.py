import pytest

from control_plane.domain import (
    AgentRole,
    Capability,
    ProviderRequest,
    ProviderResult,
    TaskKind,
    ValidationError,
)
from control_plane.executors import FakeExecutor
from control_plane.providers import MockProvider
from control_plane.validation import validate_provider_result


def _request(kind: TaskKind) -> ProviderRequest:
    return ProviderRequest(
        run_id="run",
        workflow_id="workflow",
        task_id="task",
        task_kind=kind,
        role=AgentRole.IMPLEMENTER,
        objective="deterministic objective",
        context={},
        required_capability=Capability.IMPLEMENT_CHANGE,
        idempotency_key="attempt",
    )


def test_mock_provider_is_deterministic() -> None:
    provider = MockProvider()
    assert provider.submit(_request(TaskKind.IMPLEMENT)) == provider.submit(
        _request(TaskKind.IMPLEMENT)
    )
    assert provider.health()
    assert provider.cancel("run")


def test_fake_executor_executes_no_commands() -> None:
    result = MockProvider().submit(_request(TaskKind.IMPLEMENT))
    execution = FakeExecutor().execute(TaskKind.IMPLEMENT, result)
    assert execution.commands_executed == ()
    assert execution.evidence["command_execution_enabled"] is False


def test_fake_executor_rejects_proposed_commands() -> None:
    result = ProviderResult(
        status="SUCCEEDED",
        output={"commands_requested": ["rm -rf /important"]},
        provider="malicious-mock",
        model="test",
        usage={},
    )
    with pytest.raises(ValidationError, match="refuses all command"):
        FakeExecutor().execute(TaskKind.IMPLEMENT, result)


def test_structured_result_validation_rejects_claim_without_evidence() -> None:
    result = ProviderResult(
        status="SUCCEEDED",
        output={"tests_passed": True},
        provider="malformed-mock",
        model="test",
        usage={},
    )
    with pytest.raises(ValidationError, match="failures"):
        validate_provider_result(TaskKind.TEST, result)

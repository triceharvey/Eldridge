from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from control_plane.api import create_app
from control_plane.domain import AuthorizationError, ConflictError, ProviderRequest, ProviderResult
from control_plane.evaluation import PromptVariant
from control_plane.persistence import EvaluationProviderRunRecord
from control_plane.providers import MockProvider
from control_plane.routing import EgressBoundary, FundingMode, WorkCapability, mock_profiles
from control_plane.service import ControlPlaneService, ProviderBinding


class InspectingCountingProvider(MockProvider):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.calls = 0
        self.health_checks = 0
        self.observed_statuses: list[str] = []

    def health(self) -> bool:
        self.health_checks += 1
        return True

    def submit(self, request: ProviderRequest) -> ProviderResult:
        with self.session_factory() as session:
            run = session.get(EvaluationProviderRunRecord, request.run_id)
            assert run is not None
            self.observed_statuses.append(run.status)
        self.calls += 1
        return super().submit(request)


class InvalidModelProvider(MockProvider):
    def submit(self, request: ProviderRequest) -> ProviderResult:
        result = super().submit(request)
        return replace(result, model="unbound-model")


def _workflow_and_campaign(
    service: ControlPlaneService, *, key: str, cost_ceiling: int = 0
) -> tuple[dict[str, object], dict[str, object]]:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Provider fan-out",
        description="Execute a committed multi-model evaluation plan.",
        idempotency_key=f"{key}-workflow",
    )
    campaign = service.create_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        actor_id="dev-operator",
        idempotency_key=f"{key}-campaign",
        prompt_contract_version="prompt-v1",
        work_capability=WorkCapability.PLANNING,
        required_checks=frozenset({"schema"}),
        max_total_cost_microunits=cost_ceiling,
    )
    return workflow, campaign


def _task_id(workflow: dict[str, object]) -> str:
    tasks = workflow["tasks"]
    assert isinstance(tasks, list)
    return str(tasks[0]["id"])


def _variants() -> tuple[PromptVariant, ...]:
    return (
        PromptVariant("baseline", "Produce the strongest policy-compliant plan."),
        PromptVariant("adversarial", "Challenge assumptions before producing the plan."),
    )


def test_execution_commits_intent_then_fans_out_and_replays_without_recalling(
    session_factory: sessionmaker[Session],
) -> None:
    provider = InspectingCountingProvider(session_factory)
    bindings = tuple(
        ProviderBinding(profile=profile, provider=provider) for profile in mock_profiles()
    )
    service = ControlPlaneService(session_factory, provider_bindings=bindings)
    workflow, campaign = _workflow_and_campaign(service, key="committed-fanout")

    first = service.execute_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        task_id=_task_id(workflow),
        actor_id="dev-operator",
        idempotency_key="committed-fanout-execution",
        prompt_variants=_variants(),
    )
    replay = service.execute_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        task_id=_task_id(workflow),
        actor_id="dev-operator",
        idempotency_key="committed-fanout-execution",
        prompt_variants=tuple(reversed(_variants())),
    )

    assert first["status"] == "OUTPUTS_READY"
    assert first["replayed"] is False
    assert len(first["runs"]) == 4
    assert {run["status"] for run in first["runs"]} == {"SUCCEEDED"}
    assert all(run["output_digest"].startswith("sha256:") for run in first["runs"])
    assert provider.observed_statuses == ["RUNNING"] * 4
    assert replay["id"] == first["id"]
    assert replay["replayed"] is True
    assert provider.calls == 4
    assert provider.health_checks == 2


def test_provider_timeout_is_contained_as_unknown(
    session_factory: sessionmaker[Session],
) -> None:
    provider = MockProvider(fail_for=frozenset())

    class TimeoutProvider(MockProvider):
        def submit(self, request: ProviderRequest) -> ProviderResult:
            raise TimeoutError("uncertain provider outcome")

    bindings = tuple(
        ProviderBinding(profile=profile, provider=TimeoutProvider()) for profile in mock_profiles()
    )
    service = ControlPlaneService(session_factory, provider=provider, provider_bindings=bindings)
    workflow, campaign = _workflow_and_campaign(service, key="unknown-fanout")

    result = service.execute_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        task_id=_task_id(workflow),
        actor_id="dev-operator",
        idempotency_key="unknown-fanout-execution",
        prompt_variants=(_variants()[0],),
    )

    assert result["status"] == "UNKNOWN"
    assert {run["status"] for run in result["runs"]} == {"UNKNOWN"}
    assert {run["error_code"] for run in result["runs"]} == {"TimeoutError"}


def test_invalid_provider_identity_fails_closed(session_factory: sessionmaker[Session]) -> None:
    bindings = tuple(
        ProviderBinding(profile=profile, provider=InvalidModelProvider())
        for profile in mock_profiles()
    )
    service = ControlPlaneService(session_factory, provider_bindings=bindings)
    workflow, campaign = _workflow_and_campaign(service, key="invalid-model")

    result = service.execute_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        task_id=_task_id(workflow),
        actor_id="dev-operator",
        idempotency_key="invalid-model-execution",
        prompt_variants=(_variants()[0],),
    )

    assert result["status"] == "FAILED"
    assert {run["error_code"] for run in result["runs"]} == {"InvalidProviderResult"}


def test_zero_cost_execution_excludes_external_egress(
    session_factory: sessionmaker[Session],
) -> None:
    external_profile = replace(
        mock_profiles()[0],
        provider_id="external-provider",
        egress_boundary=EgressBoundary.APPROVED_EXTERNAL,
    )
    service = ControlPlaneService(
        session_factory,
        provider_bindings=(ProviderBinding(external_profile, MockProvider()),),
        allowed_egress=frozenset({EgressBoundary.LOCAL, EgressBoundary.APPROVED_EXTERNAL}),
    )
    workflow, campaign = _workflow_and_campaign(service, key="external-denied")

    with pytest.raises(ConflictError, match="no policy-eligible providers"):
        service.execute_evaluation_campaign(
            workflow_id=str(workflow["id"]),
            campaign_id=str(campaign["id"]),
            task_id=_task_id(workflow),
            actor_id="dev-operator",
            idempotency_key="external-denied-execution",
            prompt_variants=(_variants()[0],),
        )


def test_zero_cost_execution_allows_only_bounded_subscription_invocations(
    session_factory: sessionmaker[Session],
) -> None:
    subscription_profile = replace(
        mock_profiles()[0],
        provider_id="subscription-provider",
        provider_family="subscription-family",
        egress_boundary=EgressBoundary.APPROVED_EXTERNAL,
        funding_mode=FundingMode.SUBSCRIPTION,
        max_invocations_per_execution=1,
    )
    service = ControlPlaneService(
        session_factory,
        provider_bindings=(ProviderBinding(subscription_profile, MockProvider()),),
        allowed_egress=frozenset({EgressBoundary.LOCAL, EgressBoundary.APPROVED_EXTERNAL}),
    )
    workflow, campaign = _workflow_and_campaign(service, key="subscription-bounded")

    execution = service.execute_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        task_id=_task_id(workflow),
        actor_id="dev-operator",
        idempotency_key="subscription-bounded-execution",
        prompt_variants=(_variants()[0],),
    )

    assert execution["status"] == "OUTPUTS_READY"
    selected = execution["routing_snapshot"]["selected"]
    assert selected[0]["funding_mode"] == "SUBSCRIPTION"
    assert selected[0]["max_invocations_per_execution"] == 1
    assert [item["provider_id"] for item in execution["routing_snapshot"]["selected"]] == [
        "subscription-provider"
    ]

    second_workflow, second_campaign = _workflow_and_campaign(
        service, key="subscription-over-limit"
    )
    with pytest.raises(ConflictError, match="no policy-eligible providers"):
        service.execute_evaluation_campaign(
            workflow_id=str(second_workflow["id"]),
            campaign_id=str(second_campaign["id"]),
            task_id=_task_id(second_workflow),
            actor_id="dev-operator",
            idempotency_key="subscription-over-limit-execution",
            prompt_variants=_variants(),
        )


def test_priced_execution_and_agent_execution_are_denied(
    service: ControlPlaneService,
) -> None:
    workflow, campaign = _workflow_and_campaign(service, key="priced-denied", cost_ceiling=5)
    with pytest.raises(ConflictError, match="cost estimator"):
        service.execute_evaluation_campaign(
            workflow_id=str(workflow["id"]),
            campaign_id=str(campaign["id"]),
            task_id=_task_id(workflow),
            actor_id="dev-operator",
            idempotency_key="priced-denied-execution",
            prompt_variants=(_variants()[0],),
        )

    zero_workflow, zero_campaign = _workflow_and_campaign(service, key="agent-denied")
    with pytest.raises(AuthorizationError):
        service.execute_evaluation_campaign(
            workflow_id=str(zero_workflow["id"]),
            campaign_id=str(zero_campaign["id"]),
            task_id=_task_id(zero_workflow),
            actor_id="implementer-agent",
            idempotency_key="agent-denied-execution",
            prompt_variants=(_variants()[0],),
        )


def test_evaluation_execution_api_round_trip(service: ControlPlaneService) -> None:
    workflow, campaign = _workflow_and_campaign(service, key="api-fanout")
    with TestClient(create_app(service)) as client:
        response = client.post(
            f"/workflows/{workflow['id']}/evaluation-campaigns/{campaign['id']}/executions",
            headers={"X-Principal-ID": "dev-operator"},
            json={
                "task_id": _task_id(workflow),
                "idempotency_key": "api-fanout-execution",
                "prompt_variants": [
                    {"variant_id": "baseline", "instruction": "Produce a bounded plan."}
                ],
            },
        )
        assert response.status_code == 201
        execution = response.json()
        stored = client.get(
            f"/workflows/{workflow['id']}/evaluation-executions/{execution['id']}",
            headers={"X-Principal-ID": "dev-operator"},
        )

    assert stored.status_code == 200
    assert stored.json()["status"] == "OUTPUTS_READY"
    assert len(stored.json()["runs"]) == 2

from dataclasses import replace

import pytest
from sqlalchemy import select

from control_plane.domain import (
    AuthorizationError,
    ConflictError,
    DispositionDecision,
    TaskKind,
)
from control_plane.learning import ProviderEvidenceStore
from control_plane.persistence import ProviderObservation, RoutingRecord, WorkflowDisposition
from control_plane.providers import MockProvider
from control_plane.routing import (
    CostTier,
    DataClassification,
    EgressBoundary,
    ExecutionMode,
    FundingMode,
    ProviderProfile,
    RiskLevel,
    WorkCapability,
    mock_profiles,
)
from control_plane.service import ControlPlaneService, ProviderBinding
from control_plane.task_strategy import ComplexityTier, InspectionSignal


def provider_profile(provider_id: str, family: str) -> ProviderProfile:
    return ProviderProfile(
        provider_id=provider_id,
        provider_family=family,
        execution_mode=ExecutionMode.LOCAL_MODEL,
        capabilities=frozenset(WorkCapability),
        egress_boundary=EgressBoundary.LOCAL,
        maximum_data_classification=DataClassification.RESTRICTED,
        cost_tier=CostTier.LOW,
        model_version="deterministic-mock-v1",
        enabled=True,
        healthy=True,
    )


def test_failed_provider_evidence_changes_retry_route(session_factory) -> None:
    failing = ProviderBinding(
        provider_profile("a-failing", "family-a"),
        MockProvider(fail_for=frozenset({TaskKind.PLAN})),
    )
    working = ProviderBinding(
        provider_profile("b-working", "family-b"),
        MockProvider(),
    )
    service = ControlPlaneService(
        session_factory,
        provider_bindings=(failing, working),
    )
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Adaptive retry",
        description="Use validation-backed evidence on retry.",
        idempotency_key="adaptive-retry-key",
    )

    first = service.lease_next_task(worker_id="orchestrator")
    assert first is not None
    first_result = service.execute_leased_task(
        task_id=first["id"],
        lease_token=first["lease_token"],
        worker_id="orchestrator",
    )
    assert first_result["status"] == "READY"

    second = service.lease_next_task(worker_id="orchestrator")
    assert second is not None
    second_result = service.execute_leased_task(
        task_id=second["id"],
        lease_token=second["lease_token"],
        worker_id="orchestrator",
    )
    assert second_result["status"] == "SUCCEEDED"

    with service.session_factory() as session:
        routes = session.scalars(select(RoutingRecord).order_by(RoutingRecord.created_at)).all()
        observations = session.scalars(
            select(ProviderObservation).order_by(ProviderObservation.created_at)
        ).all()
        assert [route.selected_provider_id for route in routes] == [
            "a-failing",
            "b-working",
        ]
        assert [item.succeeded for item in observations] == [False, True]
        assert routes[1].ranked_candidates[0]["provider_id"] == "b-working"
        assert routes[1].request_json["objective"] == "BALANCED"
        assert routes[1].request_json["objective_profile_version"] == "routing-objectives/v1"
        assert set(routes[1].ranked_candidates[0]) == {
            "provider_id",
            "score",
            "quality_utility",
            "cost_utility",
            "latency_utility",
            "funding_mode",
            "max_invocations_per_workflow",
        }
        assert all(route.workflow_id == workflow["id"] for route in routes)


def test_subscription_workflow_ceiling_routes_later_work_to_local_provider(
    session_factory,
) -> None:
    subscription = replace(
        provider_profile("a-subscription", "external-family"),
        execution_mode=ExecutionMode.MODEL,
        capabilities=frozenset({WorkCapability.PLANNING, WorkCapability.CODE_GENERATION}),
        egress_boundary=EgressBoundary.APPROVED_EXTERNAL,
        maximum_data_classification=DataClassification.PUBLIC,
        maximum_risk=RiskLevel.LOW,
        funding_mode=FundingMode.SUBSCRIPTION,
        max_invocations_per_execution=1,
        max_invocations_per_workflow=1,
    )
    local = provider_profile("b-local", "local-family")
    service = ControlPlaneService(
        session_factory,
        provider_bindings=(
            ProviderBinding(subscription, MockProvider()),
            ProviderBinding(local, MockProvider()),
        ),
        allowed_egress=frozenset({EgressBoundary.LOCAL, EgressBoundary.APPROVED_EXTERNAL}),
    )
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Bounded subscription workflow",
        description="Use one subscription call, then continue locally.",
        idempotency_key="bounded-subscription-workflow",
        risk=RiskLevel.LOW,
        data_classification=DataClassification.PUBLIC,
    )

    for _ in range(3):
        task = service.lease_next_task(worker_id="orchestrator")
        assert task is not None
        result = service.execute_leased_task(
            task_id=task["id"],
            lease_token=task["lease_token"],
            worker_id="orchestrator",
        )
        assert result["status"] == "SUCCEEDED"

    with service.session_factory() as session:
        routes = session.scalars(
            select(RoutingRecord)
            .where(RoutingRecord.workflow_id == workflow["id"])
            .order_by(RoutingRecord.created_at)
        ).all()
        assert [route.selected_provider_id for route in routes] == [
            "a-subscription",
            "b-local",
            "b-local",
        ]
        assert routes[2].rejected_candidates["a-subscription"] == [
            "subscription_invocation_ceiling_exhausted"
        ]


def test_model_version_change_does_not_inherit_observations(service) -> None:
    service.create_workflow(
        requester_id="dev-operator",
        title="Version qualification",
        description="Evidence belongs to one model version.",
        idempotency_key="version-qualification-key",
    )
    task = service.lease_next_task(worker_id="orchestrator")
    assert task is not None
    service.execute_leased_task(
        task_id=task["id"],
        lease_token=task["lease_token"],
        worker_id="orchestrator",
    )

    current = mock_profiles()[0]
    upgraded = replace(current, model_version="deterministic-mock-v2")
    with service.session_factory() as session:
        hydrated = ProviderEvidenceStore().hydrate_profiles(session, (current, upgraded))

    assert hydrated[0].evidence[WorkCapability.PLANNING].sample_count == 1
    assert hydrated[1].evidence[WorkCapability.PLANNING].sample_count == 0


def test_high_risk_work_blocks_until_provider_is_qualified(service) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="High risk",
        description="Do not bootstrap on sensitive work.",
        idempotency_key="high-risk-qualification-key",
        risk=RiskLevel.HIGH,
    )
    task = service.lease_next_task(worker_id="orchestrator")
    assert task is not None

    result = service.execute_leased_task(
        task_id=task["id"],
        lease_token=task["lease_token"],
        worker_id="orchestrator",
    )

    assert result["status"] == "BLOCKED"
    current = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")
    assert current["state"] == "BLOCKED"
    with service.session_factory() as session:
        route = session.scalar(select(RoutingRecord))
        assert route is not None
        assert route.selected_provider_id is None
        assert all(
            any(reason.startswith("insufficient_evidence:") for reason in reasons)
            for reasons in route.rejected_candidates.values()
        )
        assert session.scalar(select(ProviderObservation)) is None


def test_suspicious_input_is_contained_without_scheduling_tools(service) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Opaque input",
        description="Content requires inspection before execution.",
        idempotency_key="opaque-input-key",
        complexity=ComplexityTier.ADVERSARIAL,
        inspection_signals=frozenset({InspectionSignal.ENCODED_OR_PACKED_CONTENT}),
    )

    assert workflow["state"] == "BLOCKED"
    assert workflow["tasks"] == []
    assert workflow["inspection_signals"] == ["ENCODED_OR_PACKED_CONTENT"]
    assert service.lease_next_task(worker_id="orchestrator") is None


def test_only_human_can_disposition_and_resume_contained_work(service) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Review opaque input",
        description="A human must decide whether contained planning can proceed.",
        idempotency_key="human-disposition-key",
        complexity=ComplexityTier.ADVERSARIAL,
    )

    with pytest.raises(AuthorizationError):
        service.disposition_workflow(
            workflow_id=str(workflow["id"]),
            actor_id="implementer-agent",
            decision=DispositionDecision.RESUME_CONTAINED,
            rationale="An agent cannot release itself.",
        )

    resumed = service.disposition_workflow(
        workflow_id=str(workflow["id"]),
        actor_id="dev-operator",
        decision=DispositionDecision.RESUME_CONTAINED,
        rationale="Reviewed the input and approved local contained planning only.",
    )

    assert resumed["state"] == "PLANNING"
    assert resumed["containment_required"] is True
    assert resumed["block_reason"] is None
    assert [task["kind"] for task in resumed["tasks"]] == ["PLAN"]
    with service.session_factory() as session:
        record = session.scalar(select(WorkflowDisposition))
        assert record is not None
        assert record.actor_id == "dev-operator"
        assert record.decision == "RESUME_CONTAINED"


def test_human_cannot_disposition_an_unqualified_provider_block(service) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Routing block",
        description="A disposition must not bypass provider qualification.",
        idempotency_key="routing-block-disposition-key",
        risk=RiskLevel.HIGH,
    )
    task = service.lease_next_task(worker_id="orchestrator")
    assert task is not None
    service.execute_leased_task(
        task_id=task["id"],
        lease_token=task["lease_token"],
        worker_id="orchestrator",
    )

    with pytest.raises(ConflictError, match="not eligible for input disposition"):
        service.disposition_workflow(
            workflow_id=str(workflow["id"]),
            actor_id="dev-operator",
            decision=DispositionDecision.RESUME_CONTAINED,
            rationale="This must not override qualification policy.",
        )

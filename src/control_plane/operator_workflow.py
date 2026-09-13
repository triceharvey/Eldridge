from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from sqlalchemy.engine import Engine

from control_plane.activation import ProviderActivationPolicy, activate_integrations
from control_plane.domain import TaskKind, WorkflowState
from control_plane.executors import FakeExecutor, IsolatedRepositoryExecutor
from control_plane.learning import ProviderEvidenceStore
from control_plane.persistence import (
    initialize_database,
    make_engine,
    make_session_factory,
    seed_principals,
)
from control_plane.routing import (
    DataClassification,
    EgressBoundary,
    ProviderProfile,
    RiskLevel,
    RoutingObjective,
    WorkCapability,
    interoperability_profiles,
    mock_profiles,
)
from control_plane.service import ControlPlaneService, ProviderBinding
from control_plane.task_strategy import ComplexityTier
from control_plane.tools import validate_relative_path
from control_plane.workspaces import (
    GitWorktreeManager,
    RepositoryRegistration,
    RepositoryRegistry,
)

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
COMMIT_DIGEST = re.compile(r"^[0-9a-f]{40}$")
TASK_CAPABILITIES = {
    TaskKind.PLAN: WorkCapability.PLANNING,
    TaskKind.ARCHITECTURE_REVIEW: WorkCapability.ARCHITECTURE,
    TaskKind.IMPLEMENT: WorkCapability.CODE_GENERATION,
    TaskKind.TEST: WorkCapability.TEST_EXECUTION,
    TaskKind.SECURITY_REVIEW: WorkCapability.SECURITY_ANALYSIS,
    TaskKind.CODE_REVIEW: WorkCapability.CODE_REVIEW,
}
REVIEW_PRODUCERS = {
    TaskKind.ARCHITECTURE_REVIEW: TaskKind.PLAN,
    TaskKind.TEST: TaskKind.IMPLEMENT,
    TaskKind.SECURITY_REVIEW: TaskKind.IMPLEMENT,
    TaskKind.CODE_REVIEW: TaskKind.IMPLEMENT,
}


def _parse_named_enum(value: object, enum_type: type[Any]) -> object:
    if not isinstance(value, str):
        return value
    try:
        return enum_type[value]
    except KeyError as exc:
        raise ValueError(f"unknown {enum_type.__name__} name") from exc


class OperatorWorkflowManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    requester_id: str = Field(default="dev-operator", pattern=SAFE_IDENTIFIER.pattern)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=65_536)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=SAFE_IDENTIFIER.pattern)
    repository_scope: str = Field(min_length=1, max_length=200, pattern=SAFE_IDENTIFIER.pattern)
    base_revision: str = Field(pattern=COMMIT_DIGEST.pattern)
    writable_paths: tuple[str, ...] = Field(min_length=1, max_length=64)
    providers: dict[TaskKind, str]
    complexity: ComplexityTier = ComplexityTier.STANDARD
    risk: RiskLevel = RiskLevel.MEDIUM
    data_classification: DataClassification = DataClassification.INTERNAL
    routing_objective: RoutingObjective = RoutingObjective.QUALITY
    max_task_leases: int = Field(default=12, ge=6, le=48)

    @field_validator("complexity", "risk", "data_classification", mode="before")
    @classmethod
    def parse_named_limits(cls, value: object, info: ValidationInfo) -> object:
        enum_type = {
            "complexity": ComplexityTier,
            "risk": RiskLevel,
            "data_classification": DataClassification,
        }[str(info.field_name)]
        return _parse_named_enum(value, enum_type)

    @field_validator("writable_paths")
    @classmethod
    def validate_writable_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("writable paths must be unique")
        for value in values:
            validate_relative_path(value)
        return values

    @field_validator("providers")
    @classmethod
    def validate_provider_ids(cls, values: dict[TaskKind, str]) -> dict[TaskKind, str]:
        for value in values.values():
            if not SAFE_IDENTIFIER.fullmatch(value):
                raise ValueError("provider ID is invalid")
        return values

    @model_validator(mode="after")
    def require_complete_role_plan(self) -> OperatorWorkflowManifest:
        if set(self.providers) != set(TASK_CAPABILITIES):
            raise ValueError("providers must assign every model task kind exactly once")
        return self

    @classmethod
    def from_file(cls, manifest_path: Path) -> OperatorWorkflowManifest:
        path = manifest_path.resolve()
        if not path.is_file() or path.stat().st_size > 131_072:
            raise ValueError("operator workflow manifest is missing or too large")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("operator workflow manifest is invalid") from exc
        return cls.model_validate(payload)


@dataclass(frozen=True)
class OperatorRuntime:
    engine: Engine
    service: ControlPlaneService


def _configured_profiles(policy: ProviderActivationPolicy) -> dict[str, ProviderProfile]:
    profiles = {profile.provider_id: profile for profile in interoperability_profiles()}
    configured: dict[str, ProviderProfile] = {}
    if policy.include_mock_providers:
        configured.update({profile.provider_id: profile for profile in mock_profiles()})
    if policy.local_model is not None and policy.local_model.enabled:
        configured["local-openai-compatible"] = replace(
            profiles["local-openai-compatible"],
            maximum_data_classification=policy.local_model.maximum_data_classification,
            maximum_risk=policy.local_model.maximum_risk,
        )
    if policy.anthropic is not None and policy.anthropic.enabled:
        configured["anthropic-claude"] = replace(
            profiles["anthropic-claude"],
            maximum_data_classification=policy.anthropic.maximum_data_classification,
            maximum_risk=policy.anthropic.maximum_risk,
        )
    if policy.claude_code is not None and policy.claude_code.enabled:
        configured["claude-code-subscription"] = replace(
            profiles["claude-code-subscription"],
            maximum_data_classification=policy.claude_code.maximum_data_classification,
            maximum_risk=policy.claude_code.maximum_risk,
            max_invocations_per_execution=policy.claude_code.max_invocations_per_execution,
            max_invocations_per_workflow=policy.claude_code.max_invocations_per_workflow,
        )
    return configured


def preflight_operator_workflow(
    manifest: OperatorWorkflowManifest,
    *,
    repository_registry_file: Path,
    provider_policy_file: Path,
) -> dict[str, Any]:
    registry = RepositoryRegistry.from_file(repository_registry_file)
    registration = registry.resolve(manifest.repository_scope)
    resolved_base = registry.resolve_commit(manifest.repository_scope, manifest.base_revision)
    if resolved_base != manifest.base_revision:
        raise ValueError("workflow base revision must be an immutable commit digest")
    registered_base = registry.resolve_commit(manifest.repository_scope, registration.base_revision)
    if resolved_base != registered_base:
        raise ValueError("workflow base revision does not match the repository registry base")
    for requested in manifest.writable_paths:
        if not any(
            requested == allowed or requested.startswith(f"{allowed.rstrip('/')}/")
            for allowed in registration.writable_paths
        ):
            raise ValueError("workflow writable path exceeds the repository registry grant")

    policy = ProviderActivationPolicy.from_file(provider_policy_file)
    profiles = _configured_profiles(policy)
    for task_kind, provider_id in manifest.providers.items():
        profile = profiles.get(provider_id)
        if profile is None:
            raise ValueError(f"provider {provider_id} is not enabled by policy")
        if TASK_CAPABILITIES[task_kind] not in profile.capabilities:
            raise ValueError(f"provider {provider_id} cannot perform {task_kind.value}")
        if manifest.data_classification > profile.maximum_data_classification:
            raise ValueError(f"provider {provider_id} exceeds its data-classification ceiling")
        if manifest.risk > profile.maximum_risk:
            raise ValueError(f"provider {provider_id} exceeds its risk ceiling")
    for provider_id, profile in profiles.items():
        assigned_stages = sum(
            assigned_provider == provider_id for assigned_provider in manifest.providers.values()
        )
        if (
            profile.max_invocations_per_workflow > 0
            and assigned_stages > profile.max_invocations_per_workflow
        ):
            raise ValueError(f"provider {provider_id} lacks quota for its assigned stages")
    for reviewer_kind, producer_kind in REVIEW_PRODUCERS.items():
        if manifest.providers[reviewer_kind] == manifest.providers[producer_kind]:
            raise ValueError(f"{reviewer_kind.value} must use an independent provider identity")
        if manifest.risk >= RiskLevel.HIGH:
            reviewer = profiles[manifest.providers[reviewer_kind]]
            producer = profiles[manifest.providers[producer_kind]]
            if reviewer.provider_family == producer.provider_family:
                raise ValueError(f"{reviewer_kind.value} must use an independent provider family")

    external = sorted(
        {
            provider_id
            for provider_id in manifest.providers.values()
            if profiles[provider_id].egress_boundary is EgressBoundary.APPROVED_EXTERNAL
        }
    )
    return {
        "result": "READY",
        "schema_version": manifest.schema_version,
        "repository_scope": manifest.repository_scope,
        "repository_path": str(registration.path),
        "base_revision": resolved_base,
        "writable_paths": list(manifest.writable_paths),
        "provider_policy_version": policy.policy_version,
        "providers": {kind.value: manifest.providers[kind] for kind in TASK_CAPABILITIES},
        "external_providers": external,
        "requires_external_egress_confirmation": bool(external),
        "stops_at": WorkflowState.AWAITING_HUMAN_APPROVAL.value,
    }


def _narrow_bindings(
    bindings: tuple[ProviderBinding, ...], manifest: OperatorWorkflowManifest
) -> tuple[ProviderBinding, ...]:
    capabilities: dict[str, set[WorkCapability]] = {}
    for task_kind, provider_id in manifest.providers.items():
        capabilities.setdefault(provider_id, set()).add(TASK_CAPABILITIES[task_kind])
    narrowed: list[ProviderBinding] = []
    for binding in bindings:
        assigned = capabilities.get(binding.profile.provider_id)
        if assigned:
            narrowed.append(
                ProviderBinding(
                    profile=replace(binding.profile, capabilities=frozenset(assigned)),
                    provider=binding.provider,
                )
            )
    if set(capabilities) != {binding.profile.provider_id for binding in narrowed}:
        raise ValueError("an assigned provider did not activate successfully")
    return tuple(narrowed)


def build_operator_runtime(
    manifest: OperatorWorkflowManifest,
    *,
    repository_registry_file: Path,
    provider_policy_file: Path,
    database_url: str,
    worktree_root: Path,
    create_schema: bool,
) -> OperatorRuntime:
    preflight_operator_workflow(
        manifest,
        repository_registry_file=repository_registry_file,
        provider_policy_file=provider_policy_file,
    )
    broad_registry = RepositoryRegistry.from_file(repository_registry_file)
    broad_registration = broad_registry.resolve(manifest.repository_scope)
    registry = RepositoryRegistry(
        (
            RepositoryRegistration(
                scope_id=manifest.repository_scope,
                path=broad_registration.path,
                base_revision=manifest.base_revision,
                writable_paths=manifest.writable_paths,
            ),
        )
    )
    policy = ProviderActivationPolicy.from_file(provider_policy_file)
    activated = activate_integrations(policy)
    engine = make_engine(database_url)
    if create_schema:
        initialize_database(engine)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        seed_principals(session)
    service = ControlPlaneService(
        session_factory,
        executor=IsolatedRepositoryExecutor(
            registry=registry,
            worktrees=GitWorktreeManager(worktree_root),
            fallback=FakeExecutor(),
            require_tool_evidence=True,
        ),
        repository_registry=registry,
        provider_bindings=_narrow_bindings(activated.bindings, manifest),
        allowed_egress=activated.allowed_egress,
        provider_policy_version=activated.policy_version,
        routing_objective=manifest.routing_objective,
        evidence_store=ProviderEvidenceStore(window_size=100),
    )
    return OperatorRuntime(engine=engine, service=service)


def drive_operator_workflow(
    service: ControlPlaneService,
    manifest: OperatorWorkflowManifest,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    reporter = emit or (lambda _event: None)
    workflow = service.create_workflow(
        requester_id=manifest.requester_id,
        title=manifest.title,
        description=manifest.objective,
        idempotency_key=manifest.idempotency_key,
        complexity=manifest.complexity,
        risk=manifest.risk,
        data_classification=manifest.data_classification,
        repository_scope=manifest.repository_scope,
    )
    reporter({"event": "workflow_created", "workflow_id": workflow["id"]})
    leases = 0
    while workflow["state"] != WorkflowState.AWAITING_HUMAN_APPROVAL.value:
        leases += 1
        if leases > manifest.max_task_leases:
            raise RuntimeError("workflow exceeded its manifest task-lease ceiling")
        task = service.lease_next_task(worker_id="orchestrator", workflow_id=str(workflow["id"]))
        if task is None:
            raise RuntimeError("workflow has no ready task before the human gate")
        result = service.execute_leased_task(
            task_id=task["id"],
            lease_token=task["lease_token"],
            worker_id="orchestrator",
        )
        reporter(
            {
                "event": "task_attempted",
                "kind": task["kind"],
                "status": result["status"],
            }
        )
        if result["status"] not in {"READY", "SUCCEEDED"}:
            raise RuntimeError(f"workflow task failed closed: {task['kind']}")
        workflow = service.get_workflow(workflow["id"], principal_id=manifest.requester_id)
    reporter(
        {
            "event": "human_gate_reached",
            "workflow_id": workflow["id"],
            "candidate_revision": workflow["candidate_revision"],
        }
    )
    return workflow

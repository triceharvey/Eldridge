from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from control_plane.activation import ProviderActivationPolicy, activate_integrations
from control_plane.config import Settings, get_settings
from control_plane.executors import FakeExecutor, IsolatedRepositoryExecutor, TaskExecutor
from control_plane.github_app import GitHubAppClient, GitHubAppPolicy, activate_github_app
from control_plane.identity import OidcAuthenticator, OidcPolicy, activate_oidc
from control_plane.integrations import DevinRuntime
from control_plane.learning import ProviderEvidenceStore
from control_plane.persistence import (
    initialize_database,
    make_engine,
    make_session_factory,
    seed_principals,
)
from control_plane.routing import EgressBoundary, RoutingObjective
from control_plane.service import ControlPlaneService
from control_plane.workspaces import GitWorktreeManager, RepositoryRegistry


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    service: ControlPlaneService
    devin_runtime: DevinRuntime | None = None
    github_app: GitHubAppClient | None = None
    oidc_authenticator: OidcAuthenticator | None = None


def build_runtime(
    settings: Settings | None = None,
    *,
    create_schema: bool = False,
    activate_provider_clients: bool = True,
    activate_github_app_client: bool = True,
    activate_oidc_client: bool = True,
    enable_repository_execution: bool = True,
) -> Runtime:
    selected = settings or get_settings()
    activated = (
        activate_integrations(ProviderActivationPolicy.from_file(selected.provider_policy_file))
        if activate_provider_clients and selected.provider_policy_file
        else None
    )
    github_app = (
        activate_github_app(GitHubAppPolicy.from_file(selected.github_app_policy_file))
        if activate_github_app_client and selected.github_app_policy_file
        else None
    )
    oidc_authenticator = (
        activate_oidc(OidcPolicy.from_file(selected.oidc_policy_file))
        if activate_oidc_client and selected.oidc_policy_file
        else None
    )
    if (
        activate_oidc_client
        and selected.environment.lower() not in {"development", "test"}
        and oidc_authenticator is None
    ):
        raise ValueError("OIDC authentication is required outside development and test")
    if selected.metrics_enabled and selected.metrics_bearer_token is None:
        raise ValueError("metrics bearer authentication is required when metrics are enabled")
    if (
        selected.metrics_bearer_token is not None
        and len(selected.metrics_bearer_token.get_secret_value()) < 32
    ):
        raise ValueError("metrics bearer credential must contain at least 32 characters")
    engine = make_engine(selected.database_url)
    if create_schema:
        initialize_database(engine)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        seed_principals(session)
    registry = None
    executor: TaskExecutor = FakeExecutor()
    if enable_repository_execution:
        registry = (
            RepositoryRegistry.from_file(selected.repository_registry_file)
            if selected.repository_registry_file
            else RepositoryRegistry()
        )
        executor = IsolatedRepositoryExecutor(
            registry=registry,
            worktrees=GitWorktreeManager(selected.worktree_root),
            fallback=FakeExecutor(),
        )
    service = ControlPlaneService(
        session_factory,
        executor=executor,
        lease_seconds=selected.lease_seconds,
        evidence_store=ProviderEvidenceStore(window_size=selected.evidence_window_size),
        high_risk_min_evidence_samples=selected.high_risk_min_evidence_samples,
        heartbeat_interval_seconds=selected.lease_heartbeat_seconds,
        repository_registry=registry,
        provider_bindings=activated.bindings if activated else None,
        allowed_egress=(
            activated.allowed_egress if activated else frozenset({EgressBoundary.LOCAL})
        ),
        provider_policy_version=activated.policy_version if activated else "built-in/mock-v1",
        routing_objective=RoutingObjective(selected.routing_objective),
        github_app=github_app,
    )
    return Runtime(
        settings=selected,
        engine=engine,
        session_factory=session_factory,
        service=service,
        devin_runtime=activated.devin_runtime if activated else None,
        github_app=github_app,
        oidc_authenticator=oidc_authenticator,
    )

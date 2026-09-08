from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from secrets import compare_digest
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import text

from control_plane.domain import (
    ApprovalAction,
    ApprovalDecision,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ControlPlaneError,
    DispositionDecision,
    InvalidTransitionError,
    NotFoundError,
    ReconciliationDecision,
    ValidationError,
)
from control_plane.github import normalize_check_run, verify_github_signature
from control_plane.identity import OidcAuthenticator
from control_plane.observability import render_dashboard, render_prometheus
from control_plane.routing import DataClassification, RiskLevel
from control_plane.runtime import build_runtime
from control_plane.service import ControlPlaneService
from control_plane.task_strategy import ComplexityTier, InspectionSignal


class WorkflowCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str = Field(min_length=8, max_length=128)
    complexity: Literal["SIMPLE", "STANDARD", "COMPLEX", "ADVERSARIAL"] = "STANDARD"
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    data_classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] = "INTERNAL"
    repository_scope: str | None = Field(default=None, max_length=500)
    inspection_signals: frozenset[InspectionSignal] = frozenset()


class ApprovalCreate(BaseModel):
    workflow_id: str
    action: ApprovalAction = ApprovalAction.MERGE
    target: str = Field(min_length=1, max_length=500)
    revision: str = Field(min_length=1, max_length=128)
    decision: ApprovalDecision
    rationale: str = Field(min_length=1, max_length=2_000)
    expires_in_minutes: int = Field(default=15, ge=1, le=1440)


class DispositionCreate(BaseModel):
    decision: DispositionDecision
    rationale: str = Field(min_length=1, max_length=2_000)


class ReconciliationCreate(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    decision: ReconciliationDecision
    rationale: str = Field(min_length=1, max_length=2_000)


class PullRequestCreate(BaseModel):
    head_branch: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=20_000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class MergeReadinessCreate(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)


class MergeConfirmationCreate(BaseModel):
    assessment_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=128)


def create_app(
    service: ControlPlaneService | None = None,
    *,
    github_webhook_enabled: bool = False,
    github_webhook_secret: SecretStr | None = None,
    oidc_authenticator: OidcAuthenticator | None = None,
    metrics_enabled: bool = False,
    metrics_bearer_token: SecretStr | None = None,
    dashboard_enabled: bool = True,
) -> FastAPI:
    supplied_service = service

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if supplied_service is None:
            runtime = build_runtime(
                create_schema=False,
                activate_provider_clients=False,
                enable_repository_execution=False,
            )
            app.state.runtime = runtime
            app.state.service = runtime.service
            app.state.github_webhook_enabled = runtime.settings.github_webhook_enabled
            app.state.github_webhook_secret = runtime.settings.github_webhook_secret
            app.state.oidc_authenticator = runtime.oidc_authenticator
            app.state.metrics_enabled = runtime.settings.metrics_enabled
            app.state.metrics_bearer_token = runtime.settings.metrics_bearer_token
            app.state.dashboard_enabled = runtime.settings.dashboard_enabled
        else:
            app.state.service = supplied_service
            app.state.github_webhook_enabled = github_webhook_enabled
            app.state.github_webhook_secret = github_webhook_secret
            app.state.oidc_authenticator = oidc_authenticator
            app.state.metrics_enabled = metrics_enabled
            app.state.metrics_bearer_token = metrics_bearer_token
            app.state.dashboard_enabled = dashboard_enabled
        yield

    app = FastAPI(
        title="AI Engineering Control Plane",
        version="0.3.0",
        description=(
            "Phase 3 Git/CI integration control plane. OIDC is mandatory outside development "
            "and test; the development identity header is ignored when OIDC is active."
        ),
        lifespan=lifespan,
    )

    def service_dependency() -> ControlPlaneService:
        service_from_state: ControlPlaneService = app.state.service
        return service_from_state

    def principal_dependency(
        authorization: Annotated[str | None, Header()] = None,
        x_principal_id: Annotated[str, Header()] = "dev-operator",
    ) -> str:
        authenticator: OidcAuthenticator | None = app.state.oidc_authenticator
        if authenticator is not None:
            return authenticator.authenticate(authorization)
        return x_principal_id

    @app.exception_handler(ControlPlaneError)
    async def control_plane_error_handler(_request: Any, exc: ControlPlaneError) -> JSONResponse:
        if isinstance(exc, AuthenticationError):
            code = status.HTTP_401_UNAUTHORIZED
        elif isinstance(exc, NotFoundError):
            code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc, AuthorizationError):
            code = status.HTTP_403_FORBIDDEN
        elif isinstance(exc, (ConflictError, InvalidTransitionError)):
            code = status.HTTP_409_CONFLICT
        elif isinstance(exc, ValidationError):
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        else:
            code = status.HTTP_400_BAD_REQUEST
        return JSONResponse(
            status_code=code,
            content={"error": type(exc).__name__, "detail": str(exc)},
        )

    @app.post("/workflows", status_code=status.HTTP_201_CREATED)
    def create_workflow(
        request: WorkflowCreate,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.create_workflow(
            requester_id=principal_id,
            title=request.title,
            description=request.description,
            idempotency_key=request.idempotency_key,
            complexity=ComplexityTier[request.complexity],
            risk=RiskLevel[request.risk],
            data_classification=DataClassification[request.data_classification],
            repository_scope=request.repository_scope,
            inspection_signals=request.inspection_signals,
        )

    @app.get("/workflows/{workflow_id}")
    def get_workflow(
        workflow_id: str,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.get_workflow(workflow_id, principal_id=principal_id)

    @app.post("/workflows/{workflow_id}/cancel")
    def cancel_workflow(
        workflow_id: str,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.cancel_workflow(workflow_id, principal_id=principal_id)

    @app.post("/workflows/{workflow_id}/disposition")
    def disposition_workflow(
        workflow_id: str,
        request: DispositionCreate,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.disposition_workflow(
            workflow_id=workflow_id,
            actor_id=principal_id,
            decision=request.decision,
            rationale=request.rationale,
        )

    @app.post("/workflows/{workflow_id}/reconcile")
    def reconcile_execution(
        workflow_id: str,
        request: ReconciliationCreate,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.reconcile_execution(
            workflow_id=workflow_id,
            task_id=request.task_id,
            actor_id=principal_id,
            decision=request.decision,
            rationale=request.rationale,
        )

    @app.post("/approvals", status_code=status.HTTP_201_CREATED)
    def create_approval(
        request: ApprovalCreate,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.approve(
            workflow_id=request.workflow_id,
            approver_id=principal_id,
            action=request.action,
            target=request.target,
            revision=request.revision,
            decision=request.decision,
            rationale=request.rationale,
            expires_in_minutes=request.expires_in_minutes,
        )

    @app.get("/events")
    def list_events(
        workflow_id: Annotated[str, Query()],
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> list[dict[str, Any]]:
        return service.list_events(workflow_id, principal_id=principal_id)

    @app.get("/routing-decisions")
    def list_routing_decisions(
        workflow_id: Annotated[str, Query()],
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> list[dict[str, Any]]:
        return service.list_routing_records(workflow_id, principal_id=principal_id)

    @app.get("/provider-evidence")
    def list_provider_evidence(
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> list[dict[str, Any]]:
        return service.list_provider_evidence(principal_id=principal_id)

    @app.get("/ci-checks")
    def list_ci_checks(
        workflow_id: Annotated[str, Query()],
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> list[dict[str, Any]]:
        return service.list_ci_check_evidence(workflow_id, principal_id=principal_id)

    @app.post("/workflows/{workflow_id}/pull-requests", status_code=status.HTTP_201_CREATED)
    def propose_pull_request(
        workflow_id: str,
        request: PullRequestCreate,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.propose_pull_request(
            workflow_id=workflow_id,
            actor_id=principal_id,
            head_branch=request.head_branch,
            title=request.title,
            body=request.body,
            idempotency_key=request.idempotency_key,
        )

    @app.get("/workflows/{workflow_id}/pull-requests")
    def list_pull_requests(
        workflow_id: str,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> list[dict[str, Any]]:
        return service.list_pull_request_proposals(workflow_id, principal_id=principal_id)

    @app.post("/workflows/{workflow_id}/pull-requests/{proposal_id}/reconcile")
    def reconcile_pull_request(
        workflow_id: str,
        proposal_id: str,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.reconcile_pull_request(
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            actor_id=principal_id,
        )

    @app.post("/workflows/{workflow_id}/pull-requests/{proposal_id}/readiness")
    def assess_merge_readiness(
        workflow_id: str,
        proposal_id: str,
        request: MergeReadinessCreate,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.assess_merge_readiness(
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            actor_id=principal_id,
            idempotency_key=request.idempotency_key,
        )

    @app.post("/workflows/{workflow_id}/pull-requests/{proposal_id}/confirm-merged")
    def confirm_pull_request_merged(
        workflow_id: str,
        proposal_id: str,
        request: MergeConfirmationCreate,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.confirm_pull_request_merged(
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            assessment_id=request.assessment_id,
            actor_id=principal_id,
            idempotency_key=request.idempotency_key,
        )

    @app.get("/workflows/{workflow_id}/merge-confirmations")
    def list_merge_confirmations(
        workflow_id: str,
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> list[dict[str, Any]]:
        return service.list_merge_confirmations(workflow_id, principal_id=principal_id)

    @app.post("/integrations/github/webhook")
    async def github_webhook(
        request: Request,
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
        x_github_event: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
        x_github_delivery: Annotated[str | None, Header(alias="X-GitHub-Delivery")] = None,
        x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
    ) -> dict[str, Any]:
        if not app.state.github_webhook_enabled:
            raise HTTPException(status_code=404, detail="integration disabled")
        secret: SecretStr | None = app.state.github_webhook_secret
        if secret is None:
            raise HTTPException(status_code=503, detail="webhook secret unavailable")
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > 1_048_576:
            raise HTTPException(status_code=413, detail="webhook payload too large")
        body = await request.body()
        if len(body) > 1_048_576:
            raise HTTPException(status_code=413, detail="webhook payload too large")
        verify_github_signature(body, x_hub_signature_256, secret.get_secret_value())
        if x_github_event != "check_run" or x_github_delivery is None:
            raise ValidationError("only GitHub check_run deliveries are accepted")
        check = normalize_check_run(body, delivery_id=x_github_delivery)
        if check is None:
            return {"accepted": False, "reason": "check_run_not_completed"}
        return service.ingest_ci_check_evidence(
            actor_id="github-ci",
            delivery_id=x_github_delivery,
            repository=check.repository,
            check_run_id=check.check_run_id,
            check_name=check.check_name,
            revision=check.revision,
            status=check.status,
            conclusion=check.conclusion,
            details_url=check.details_url,
            app_slug=check.app_slug,
            payload_digest=sha256(body).hexdigest(),
        )

    @app.get("/health/live")
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness(
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, str]:
        try:
            with service.session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ready"}

    @app.get("/operations/summary")
    def operations_summary(
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> dict[str, Any]:
        return service.operational_snapshot(principal_id=principal_id)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(
        principal_id: Annotated[str, Depends(principal_dependency)],
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
    ) -> HTMLResponse:
        if not app.state.dashboard_enabled:
            raise HTTPException(status_code=404, detail="dashboard disabled")
        body = render_dashboard(service.operational_snapshot(principal_id=principal_id))
        return HTMLResponse(
            body,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                    "frame-ancestors 'none'; form-action 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/metrics", include_in_schema=False)
    def metrics(
        service: Annotated[ControlPlaneService, Depends(service_dependency)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        if not app.state.metrics_enabled:
            raise HTTPException(status_code=404, detail="metrics disabled")
        secret: SecretStr | None = app.state.metrics_bearer_token
        expected = secret.get_secret_value() if secret else ""
        parts = authorization.split(" ") if authorization else []
        supplied = (
            parts[1]
            if len(parts) == 2
            and parts[0].lower() == "bearer"
            and authorization is not None
            and "," not in authorization
            else ""
        )
        if not expected or not supplied or not compare_digest(supplied, expected):
            raise HTTPException(
                status_code=401,
                detail="valid monitoring credential required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return Response(
            content=render_prometheus(service.operational_snapshot()),
            headers={"Cache-Control": "no-store", "Content-Type": CONTENT_TYPE_LATEST},
        )

    return app


app = create_app()


def run() -> None:
    uvicorn.run("control_plane.api:app", host="127.0.0.1", port=8000, reload=False)

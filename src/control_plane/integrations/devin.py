from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from control_plane.domain import IntegrationDisabledError, IntegrationResponseError, ValidationError
from control_plane.integrations.base import (
    RemoteAgentHandle,
    RemoteAgentRequest,
    RemoteAgentStatus,
    RemoteRunState,
)
from control_plane.routing import DataClassification, RiskLevel
from control_plane.secrets import SecretResolver

DEVIN_API_BASE = "https://api.devin.ai/v3"
DEVIN_ID_PATTERN = re.compile(r"^devin-[A-Za-z0-9_-]{1,194}$")


class DevinRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    api_base: str = DEVIN_API_BASE
    organization_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    service_token_ref: str = Field(min_length=1)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    maximum_cost_units: int = Field(default=2, ge=1, le=100)
    maximum_data_classification: DataClassification = DataClassification.INTERNAL
    maximum_risk: RiskLevel = RiskLevel.MEDIUM

    @model_validator(mode="after")
    def restrict_endpoint(self) -> DevinRuntimeConfig:
        if self.api_base != DEVIN_API_BASE:
            raise ValueError("Phase 2 permits only the official Devin v3 API endpoint")
        return self


class DevinRuntime:
    """Devin v3 remote-session runtime with explicit lifecycle boundaries."""

    def __init__(
        self,
        config: DevinRuntimeConfig,
        secret_resolver: SecretResolver,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.secret_resolver = secret_resolver
        self.client = client or httpx.Client(timeout=config.timeout_seconds)

    @property
    def name(self) -> str:
        return "devin"

    def submit(self, request: RemoteAgentRequest) -> RemoteAgentHandle:
        if request.max_cost_units < 1 or not request.repositories:
            raise ValidationError("Devin requires repository scope and a positive cost-unit limit")
        if request.max_cost_units > self.config.maximum_cost_units:
            raise ValidationError("Devin request exceeds the configured cost-unit limit")
        if request.data_classification > self.config.maximum_data_classification:
            raise ValidationError("Devin request exceeds the configured data-classification limit")
        if request.risk > self.config.maximum_risk:
            raise ValidationError("Devin request exceeds the configured risk limit")
        payload: dict[str, Any] = {
            "prompt": request.objective,
            "title": f"Control-plane task {request.task_id}",
            "repos": list(request.repositories),
            "max_acu_limit": request.max_cost_units,
            "tags": [
                *request.tags,
                f"workflow:{request.workflow_id}",
                f"task:{request.task_id}",
                f"idempotency:{request.idempotency_key}",
            ],
        }
        if request.structured_output_schema is not None:
            payload["structured_output_schema"] = request.structured_output_schema
        response = self._request(
            "POST",
            f"/organizations/{self.config.organization_id}/sessions",
            json=payload,
        )
        body = self._json(response)
        remote_id = body.get("session_id")
        if not isinstance(remote_id, str) or not remote_id:
            raise IntegrationResponseError("Devin response lacks a session ID")
        url = body.get("url")
        return RemoteAgentHandle(
            provider=self.name,
            remote_id=remote_id,
            url=url if isinstance(url, str) else None,
        )

    def poll(self, handle: RemoteAgentHandle) -> RemoteAgentStatus:
        self._validate_handle(handle)
        response = self._request(
            "GET",
            f"/organizations/{self.config.organization_id}/sessions/{handle.remote_id}",
        )
        body = self._json(response)
        raw_status = str(body.get("status", "unknown")).lower()
        mapping = {
            "new": RemoteRunState.QUEUED,
            "queued": RemoteRunState.QUEUED,
            "claimed": RemoteRunState.RUNNING,
            "running": RemoteRunState.RUNNING,
            "suspended": RemoteRunState.SUSPENDED,
            "resuming": RemoteRunState.RUNNING,
            "exit": RemoteRunState.SUCCEEDED,
            "error": RemoteRunState.FAILED,
            "terminated": RemoteRunState.CANCELLED,
        }
        return RemoteAgentStatus(
            handle=handle,
            state=mapping.get(raw_status, RemoteRunState.UNKNOWN),
            raw_status=raw_status,
            output={key: value for key, value in body.items() if key not in {"secrets", "token"}},
        )

    def cancel(self, handle: RemoteAgentHandle) -> bool:
        self._validate_handle(handle)
        response = self._request(
            "DELETE",
            f"/organizations/{self.config.organization_id}/sessions/{handle.remote_id}",
        )
        return response.status_code in {200, 202, 204}

    def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        if not self.config.enabled:
            raise IntegrationDisabledError("Devin runtime is disabled by policy")
        token = self.secret_resolver.resolve(self.config.service_token_ref)
        response = self.client.request(
            method,
            f"{self.config.api_base}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=json,
        )
        if response.status_code >= 400:
            raise IntegrationResponseError(f"Devin request failed with HTTP {response.status_code}")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise IntegrationResponseError("Devin returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise IntegrationResponseError("Devin returned an invalid response object")
        return body

    def _validate_handle(self, handle: RemoteAgentHandle) -> None:
        if handle.provider != self.name or not DEVIN_ID_PATTERN.fullmatch(handle.remote_id):
            raise IntegrationResponseError("remote handle does not belong to Devin")

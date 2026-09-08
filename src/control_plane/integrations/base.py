from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from control_plane.routing import DataClassification, RiskLevel


class RemoteRunState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUSPENDED = "SUSPENDED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RemoteAgentRequest:
    run_id: str
    workflow_id: str
    task_id: str
    objective: str
    repositories: tuple[str, ...]
    max_cost_units: int
    idempotency_key: str
    tags: tuple[str, ...] = ()
    structured_output_schema: dict[str, object] | None = None
    data_classification: DataClassification = DataClassification.INTERNAL
    risk: RiskLevel = RiskLevel.MEDIUM


@dataclass(frozen=True)
class RemoteAgentHandle:
    provider: str
    remote_id: str
    url: str | None


@dataclass(frozen=True)
class RemoteAgentStatus:
    handle: RemoteAgentHandle
    state: RemoteRunState
    raw_status: str
    output: dict[str, object]


class RemoteAgentRuntime(Protocol):
    @property
    def name(self) -> str: ...

    def submit(self, request: RemoteAgentRequest) -> RemoteAgentHandle: ...

    def poll(self, handle: RemoteAgentHandle) -> RemoteAgentStatus: ...

    def cancel(self, handle: RemoteAgentHandle) -> bool: ...

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.domain import (
    ROLE_CAPABILITIES,
    AgentRole,
    AuthorizationError,
    Capability,
    PrincipalType,
)
from control_plane.persistence import CapabilityGrant, Principal


class PolicyEngine:
    def authorize(
        self,
        session: Session,
        principal_id: str,
        capability: Capability,
        *,
        workflow_id: str | None = None,
        task_id: str | None = None,
        require_human: bool = False,
    ) -> Principal:
        principal = session.get(Principal, principal_id)
        if principal is None or not principal.active:
            raise AuthorizationError("principal is unknown or inactive")
        if require_human and principal.principal_type != PrincipalType.HUMAN.value:
            raise AuthorizationError("this action requires an authenticated human principal")

        role = AgentRole(principal.role)
        if capability not in ROLE_CAPABILITIES[role]:
            raise AuthorizationError(f"role {role.value} lacks capability {capability.value}")

        task_scoped_identity = principal.principal_type in {
            PrincipalType.AGENT.value,
            PrincipalType.INTEGRATION.value,
        }
        unscoped_integration_capabilities = {
            Capability.CLAIM_IDE_TASK,
            Capability.SUBMIT_CI_EVIDENCE,
        }
        if task_scoped_identity and capability not in unscoped_integration_capabilities:
            if workflow_id is None or task_id is None:
                raise AuthorizationError(
                    "agent or integration action requires workflow and task scope"
                )
            grant = session.scalar(
                select(CapabilityGrant).where(
                    CapabilityGrant.principal_id == principal_id,
                    CapabilityGrant.workflow_id == workflow_id,
                    CapabilityGrant.task_id == task_id,
                    CapabilityGrant.active.is_(True),
                )
            )
            now = datetime.now(UTC)
            if grant is None or self._as_utc(grant.expires_at) <= now:
                raise AuthorizationError("no active task-scoped capability grant")
            if capability.value not in grant.capabilities:
                raise AuthorizationError("task grant does not include requested capability")
        return principal

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

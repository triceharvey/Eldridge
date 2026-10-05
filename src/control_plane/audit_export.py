from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.audit import GENESIS_HASH, verify_audit_chain
from control_plane.persistence import (
    Artifact,
    AuditEvent,
    CiCheckEvidence,
    Task,
    TaskAttempt,
    Workflow,
)

SCHEMA_VERSION = "1"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _bundle_digest(body: Mapping[str, object]) -> str:
    return f"sha256:{sha256(_canonical_json(dict(body))).hexdigest()}"


def _audit_event_dict(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "workflow_id": event.workflow_id,
        "sequence": event.sequence,
        "event_type": event.event_type,
        "actor_id": event.actor_id,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "outcome": event.outcome,
        "payload": event.payload,
        "previous_hash": event.previous_hash,
        "event_hash": event.event_hash,
        "created_at": event.created_at.isoformat(),
    }


def build_audit_export(
    session: Session,
    workflow_id: str,
    *,
    exported_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a content-free, project-bound audit and quality-evidence bundle."""

    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise ValueError("workflow was not found")
    if not verify_audit_chain(session, workflow_id):
        raise ValueError("workflow audit chain failed verification")

    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workflow_id == workflow_id)
        .order_by(AuditEvent.sequence)
    ).all()
    tasks = session.scalars(
        select(Task).where(Task.workflow_id == workflow_id).order_by(Task.position, Task.id)
    ).all()
    task_ids = [task.id for task in tasks]
    attempts = (
        session.scalars(
            select(TaskAttempt)
            .where(TaskAttempt.task_id.in_(task_ids))
            .order_by(TaskAttempt.task_id, TaskAttempt.attempt_number)
        ).all()
        if task_ids
        else []
    )
    artifacts = session.scalars(
        select(Artifact)
        .where(Artifact.workflow_id == workflow_id)
        .order_by(Artifact.created_at, Artifact.id)
    ).all()
    checks = session.scalars(
        select(CiCheckEvidence)
        .where(CiCheckEvidence.workflow_id == workflow_id)
        .order_by(CiCheckEvidence.created_at, CiCheckEvidence.id)
    ).all()

    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": (exported_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "workflow": {
            "id": workflow.id,
            "repository_scope": workflow.repository_scope,
            "state": workflow.state,
            "version": workflow.version,
            "risk_class": workflow.risk_class,
            "complexity_tier": workflow.complexity_tier,
            "data_classification": workflow.data_classification,
            "policy_version": workflow.policy_version,
            "candidate_revision": workflow.candidate_revision,
            "merged_revision": workflow.merged_revision,
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat(),
        },
        "quality_evidence": {
            "tasks": [
                {
                    "id": task.id,
                    "kind": task.kind,
                    "status": task.status,
                    "position": task.position,
                    "work_capability": task.work_capability,
                    "max_attempts": task.max_attempts,
                }
                for task in tasks
            ],
            "attempts": [
                {
                    "id": attempt.id,
                    "task_id": attempt.task_id,
                    "attempt_number": attempt.attempt_number,
                    "agent_id": attempt.agent_id,
                    "provider": attempt.provider,
                    "model": attempt.model,
                    "status": attempt.status,
                    "error_code": attempt.error_code,
                    "started_at": attempt.started_at.isoformat(),
                    "completed_at": (
                        attempt.completed_at.isoformat() if attempt.completed_at else None
                    ),
                }
                for attempt in attempts
            ],
            "artifacts": [
                {
                    "id": artifact.id,
                    "task_id": artifact.task_id,
                    "attempt_id": artifact.attempt_id,
                    "artifact_type": artifact.artifact_type,
                    "digest": artifact.digest,
                    "revision": artifact.revision,
                    "created_at": artifact.created_at.isoformat(),
                }
                for artifact in artifacts
            ],
            "ci_checks": [
                {
                    "id": check.id,
                    "source": check.source,
                    "repository": check.repository,
                    "check_run_id": check.check_run_id,
                    "check_name": check.check_name,
                    "revision": check.revision,
                    "status": check.status,
                    "conclusion": check.conclusion,
                    "app_slug": check.app_slug,
                    "payload_digest": check.payload_digest,
                    "created_at": check.created_at.isoformat(),
                }
                for check in checks
            ],
        },
        "audit_chain": {
            "event_count": len(events),
            "head_event_hash": events[-1].event_hash,
            "events": [_audit_event_dict(event) for event in events],
        },
    }
    return {**body, "bundle_digest": _bundle_digest(body)}


def build_provider_usage_report(
    session: Session,
    *,
    workflow_id: str | None = None,
    provider_id: str | None = None,
) -> dict[str, Any]:
    """Summarize recorded provider attempts without exposing prompts or model output."""

    if workflow_id is not None:
        if session.get(Workflow, workflow_id) is None:
            raise ValueError("workflow was not found")
        workflow_ids = [workflow_id]
    else:
        workflow_ids = list(session.scalars(select(Workflow.id).order_by(Workflow.id)))

    for candidate_id in workflow_ids:
        if not verify_audit_chain(session, candidate_id):
            raise ValueError("workflow audit chain failed verification")

    routed_attempts: dict[str, str] = {}
    succeeded_attempts: set[str] = set()
    if workflow_ids:
        events = session.execute(
            select(AuditEvent.event_type, AuditEvent.payload)
            .where(AuditEvent.workflow_id.in_(workflow_ids))
            .where(AuditEvent.event_type.in_(("task.routed", "task.succeeded")))
        )
        for event_type, payload in events:
            attempt_id = payload.get("attempt_id")
            if not isinstance(attempt_id, str):
                continue
            if event_type == "task.routed" and isinstance(payload.get("selected_provider_id"), str):
                routed_attempts[attempt_id] = payload["selected_provider_id"]
            elif event_type == "task.succeeded":
                succeeded_attempts.add(attempt_id)

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    if workflow_ids:
        rows = session.execute(
            select(
                Task.workflow_id,
                TaskAttempt.id,
                TaskAttempt.provider,
                TaskAttempt.model,
                TaskAttempt.status,
                TaskAttempt.started_at,
                TaskAttempt.completed_at,
            )
            .join(Task, Task.id == TaskAttempt.task_id)
            .where(Task.workflow_id.in_(workflow_ids))
            .order_by(Task.workflow_id, TaskAttempt.started_at, TaskAttempt.id)
        )
        for row in rows:
            if provider_id is not None and row.provider != provider_id:
                continue
            key = (row.provider, row.model)
            group = groups.setdefault(
                key,
                {
                    "provider": row.provider,
                    "model": row.model,
                    "attempts": 0,
                    "audit_correlated_successes": 0,
                    "status_counts": {},
                    "last_started_at": None,
                    "last_completed_at": None,
                    "workflow_ids": set(),
                },
            )
            group["attempts"] += 1
            if (
                row.status == "SUCCEEDED"
                and row.id in succeeded_attempts
                and routed_attempts.get(row.id) == row.provider
            ):
                group["audit_correlated_successes"] += 1
            counts = group["status_counts"]
            counts[row.status] = counts.get(row.status, 0) + 1
            group["workflow_ids"].add(row.workflow_id)
            started = _utc_isoformat(row.started_at)
            if group["last_started_at"] is None or started > group["last_started_at"]:
                group["last_started_at"] = started
            if row.completed_at is not None:
                completed = _utc_isoformat(row.completed_at)
                if group["last_completed_at"] is None or completed > group["last_completed_at"]:
                    group["last_completed_at"] = completed

    providers = []
    for key in sorted(groups):
        group = groups[key]
        providers.append(
            {
                **group,
                "status_counts": dict(sorted(group["status_counts"].items())),
                "workflow_ids": sorted(group["workflow_ids"]),
            }
        )
    return {
        "source": "local_control_plane_database",
        "workflow_id": workflow_id,
        "provider_filter": provider_id,
        "scanned_workflows": len(workflow_ids),
        "matching_attempts": sum(group["attempts"] for group in providers),
        "verified_workflows": len(workflow_ids),
        "providers": providers,
    }


def _utc_isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def verify_audit_export(bundle: object) -> bool:
    """Verify the bundle digest and embedded per-workflow audit chain."""

    if not isinstance(bundle, Mapping):
        return False
    body = dict(bundle)
    digest = body.pop("bundle_digest", None)
    if not isinstance(digest, str) or digest != _bundle_digest(body):
        return False
    if body.get("schema_version") != SCHEMA_VERSION:
        return False
    workflow = body.get("workflow")
    audit_chain = body.get("audit_chain")
    if not isinstance(workflow, Mapping) or not isinstance(audit_chain, Mapping):
        return False
    workflow_id = workflow.get("id")
    events = audit_chain.get("events")
    if not isinstance(workflow_id, str) or not isinstance(events, list) or not events:
        return False
    if audit_chain.get("event_count") != len(events):
        return False

    previous_hash = GENESIS_HASH
    for expected_sequence, raw_event in enumerate(events, start=1):
        if not isinstance(raw_event, Mapping):
            return False
        if (
            raw_event.get("workflow_id") != workflow_id
            or raw_event.get("sequence") != expected_sequence
            or raw_event.get("previous_hash") != previous_hash
        ):
            return False
        event_hash = raw_event.get("event_hash")
        canonical_event = {
            key: raw_event.get(key)
            for key in (
                "workflow_id",
                "sequence",
                "event_type",
                "actor_id",
                "resource_type",
                "resource_id",
                "outcome",
                "payload",
                "previous_hash",
            )
        }
        expected_hash = sha256(_canonical_json(canonical_event)).hexdigest()
        if not isinstance(event_hash, str) or event_hash != expected_hash:
            return False
        previous_hash = event_hash
    return audit_chain.get("head_event_hash") == previous_hash

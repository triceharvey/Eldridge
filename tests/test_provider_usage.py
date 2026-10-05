from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from control_plane.audit_export import build_provider_usage_report
from control_plane.cli import main
from control_plane.persistence import (
    AuditEvent,
    TaskAttempt,
    initialize_database,
    make_engine,
    make_session_factory,
    seed_principals,
)
from control_plane.service import ControlPlaneService


def _record_one_attempt(service: ControlPlaneService) -> str:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Provider usage evidence",
        description="Exercise one synthetic provider attempt.",
        idempotency_key="provider-usage-evidence",
    )
    task = service.lease_next_task(worker_id="orchestrator")
    assert task is not None
    service.execute_leased_task(
        task_id=task["id"],
        lease_token=task["lease_token"],
        worker_id="orchestrator",
    )
    return str(workflow["id"])


def test_provider_usage_reports_metadata_only(service: ControlPlaneService) -> None:
    workflow_id = _record_one_attempt(service)
    with service.session_factory() as session, session.begin():
        attempt = session.scalar(select(TaskAttempt))
        assert attempt is not None
        attempt.output = {"private_prompt": "DO_NOT_EXPORT"}

    with service.session_factory() as session:
        report = build_provider_usage_report(session, workflow_id=workflow_id)

    assert report["scanned_workflows"] == 1
    assert report["matching_attempts"] == 1
    assert report["verified_workflows"] == 1
    assert report["providers"][0]["status_counts"] == {"SUCCEEDED": 1}
    assert report["providers"][0]["audit_correlated_successes"] == 1
    assert report["providers"][0]["workflow_ids"] == [workflow_id]
    assert "DO_NOT_EXPORT" not in json.dumps(report)
    assert "private_prompt" not in json.dumps(report)


def test_provider_usage_filter_and_unknown_workflow(service: ControlPlaneService) -> None:
    workflow_id = _record_one_attempt(service)
    with service.session_factory() as session:
        report = build_provider_usage_report(
            session, workflow_id=workflow_id, provider_id="claude-code-subscription"
        )
        assert report["matching_attempts"] == 0
        assert report["providers"] == []
        with pytest.raises(ValueError, match="workflow was not found"):
            build_provider_usage_report(session, workflow_id="unknown")


def test_provider_usage_rejects_tampered_audit_chain(service: ControlPlaneService) -> None:
    workflow_id = _record_one_attempt(service)
    with service.session_factory() as session, session.begin():
        event = session.scalar(select(AuditEvent).where(AuditEvent.workflow_id == workflow_id))
        assert event is not None
        event.payload = {"tampered": True}

    with service.session_factory() as session:
        with pytest.raises(ValueError, match="audit chain failed verification"):
            build_provider_usage_report(session, workflow_id=workflow_id)


def test_provider_usage_does_not_correlate_changed_attempt_provider(
    service: ControlPlaneService,
) -> None:
    workflow_id = _record_one_attempt(service)
    with service.session_factory() as session, session.begin():
        attempt = session.scalar(select(TaskAttempt))
        assert attempt is not None
        attempt.provider = "unrecorded-provider"

    with service.session_factory() as session:
        report = build_provider_usage_report(session, workflow_id=workflow_id)

    assert report["providers"][0]["status_counts"] == {"SUCCEEDED": 1}
    assert report["providers"][0]["audit_correlated_successes"] == 0


def test_cli_provider_usage_reads_existing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database_path = tmp_path / "usage.db"
    database_url = f"sqlite:///{database_path}"
    engine = make_engine(database_url)
    initialize_database(engine)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        seed_principals(session)
    workflow_id = _record_one_attempt(ControlPlaneService(session_factory))
    engine.dispose()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control-plane",
            "audit",
            "provider-usage",
            "--database-url",
            database_url,
            "--workflow-id",
            workflow_id,
        ],
    )
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["matching_attempts"] == 1
    assert report["providers"][0]["status_counts"] == {"SUCCEEDED": 1}
    assert report["providers"][0]["audit_correlated_successes"] == 1


def test_cli_provider_usage_refuses_missing_sqlite_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "missing.db"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control-plane",
            "audit",
            "provider-usage",
            "--database-url",
            f"sqlite:///{database_path}",
        ],
    )
    with pytest.raises(SystemExit, match="provider usage database does not exist"):
        main()
    assert not database_path.exists()

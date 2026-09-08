from control_plane.integrations import WindsurfHandoff


def test_windsurf_handoff_is_revision_bound_and_deterministic() -> None:
    handoff = WindsurfHandoff()
    values = {
        "workflow_id": "workflow-1",
        "task_id": "task-1",
        "repository": "owner/repository",
        "branch": "codex/task-task-1",
        "base_revision": "abc123",
        "objective": "Implement only the assigned change.",
        "assigned_principal": "windsurf-cascade",
        "policy_version": "phase1-v1",
        "writable_paths": ("src", "tests"),
    }
    first = handoff.create(**values)
    second = handoff.create(**values)
    assert first == second
    assert first.integration_mode == "git-and-mcp-handoff"
    assert "result revision" in first.required_evidence
    assert len(first.digest) == 64

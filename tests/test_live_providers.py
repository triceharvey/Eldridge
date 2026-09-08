import os
from pathlib import Path

import pytest

from control_plane.activation import ProviderActivationPolicy, activate_integrations
from control_plane.domain import AgentRole, Capability, ProviderRequest, TaskKind
from control_plane.integrations import RemoteAgentRequest


def _activated() -> object:
    policy_path = os.getenv("CONTROL_PLANE_PROVIDER_POLICY_FILE")
    if not policy_path:
        pytest.skip("CONTROL_PLANE_PROVIDER_POLICY_FILE is not configured")
    return activate_integrations(ProviderActivationPolicy.from_file(Path(policy_path)))


@pytest.mark.live_provider
def test_live_anthropic_minimal_structured_response() -> None:
    if os.getenv("CONTROL_PLANE_RUN_LIVE_ANTHROPIC_TEST") != "true":
        pytest.skip("explicit live Anthropic opt-in is not enabled")
    activated = _activated()
    binding = next(
        (
            item
            for item in activated.bindings  # type: ignore[attr-defined]
            if item.profile.provider_id == "anthropic-claude"
        ),
        None,
    )
    if binding is None:
        pytest.fail("Anthropic is not enabled in the provider policy")
    result = binding.provider.submit(
        ProviderRequest(
            run_id="live-anthropic",
            workflow_id="live-anthropic",
            task_id="live-anthropic",
            task_kind=TaskKind.PLAN,
            role=AgentRole.ARCHITECT,
            objective='Return exactly this JSON object: {"live_probe": true}',
            context={},
            required_capability=Capability.PRODUCE_PLAN,
            idempotency_key="live-anthropic",
        )
    )
    assert result.output.get("live_probe") is True


@pytest.mark.live_provider
def test_live_devin_create_then_cancel() -> None:
    if os.getenv("CONTROL_PLANE_RUN_LIVE_DEVIN_TEST") != "true":
        pytest.skip("explicit live Devin opt-in is not enabled")
    repository = os.getenv("CONTROL_PLANE_LIVE_DEVIN_REPOSITORY")
    if not repository:
        pytest.skip("CONTROL_PLANE_LIVE_DEVIN_REPOSITORY is not configured")
    activated = _activated()
    runtime = activated.devin_runtime  # type: ignore[attr-defined]
    if runtime is None:
        pytest.fail("Devin is not enabled in the provider policy")
    handle = runtime.submit(
        RemoteAgentRequest(
            run_id="live-devin",
            workflow_id="live-devin",
            task_id="live-devin",
            objective="Inspect the repository and stop without making changes.",
            repositories=(repository,),
            max_cost_units=1,
            idempotency_key="live-devin",
            tags=("control-plane-live-probe",),
        )
    )
    assert handle.remote_id.startswith("devin-")
    assert runtime.cancel(handle)

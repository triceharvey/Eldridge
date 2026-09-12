import json
import subprocess
from pathlib import Path

import pytest

from control_plane.domain import (
    AgentRole,
    Capability,
    IntegrationDisabledError,
    IntegrationResponseError,
    ProviderRequest,
    TaskKind,
)
from control_plane.providers import ClaudeCodeProvider, ClaudeCodeProviderConfig


def _request() -> ProviderRequest:
    return ProviderRequest(
        run_id="run-1",
        workflow_id="workflow-1",
        task_id="task-1",
        task_kind=TaskKind.PLAN,
        role=AgentRole.ARCHITECT,
        objective="Return a constrained plan.",
        context={"input": "untrusted"},
        required_capability=Capability.PRODUCE_PLAN,
        idempotency_key="attempt-1",
    )


def _completed(
    command: list[str], stdout: str, returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def test_claude_code_is_disabled_by_default() -> None:
    provider = ClaudeCodeProvider(ClaudeCodeProviderConfig())
    with pytest.raises(IntegrationDisabledError, match="disabled"):
        provider.submit(_request())


def test_claude_code_uses_subscription_auth_without_tools_or_repository_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-runner")
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def runner(
        command: list[str], cwd: Path, environment: dict[str, str], _timeout: float
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd, environment))
        assert "ANTHROPIC_API_KEY" not in environment
        if command[1:3] == ["auth", "status"]:
            return _completed(
                command,
                json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "subscriptionType": "pro",
                    }
                ),
            )
        return _completed(
            command,
            json.dumps(
                {
                    "structured_output": {"plan": ["bounded step"]},
                    "usage": {"input_tokens": 12, "output_tokens": 7},
                }
            ),
        )

    provider = ClaudeCodeProvider(
        ClaudeCodeProviderConfig(enabled=True, model="sonnet"), runner=runner
    )
    result = provider.submit(_request())

    assert result.output == {"plan": ["bounded step"]}
    assert result.provider == "claude-code-subscription"
    assert result.usage == {"input_tokens": 12, "output_tokens": 7}
    command = calls[1][0]
    assert command[command.index("--tools") + 1] == ""
    assert "--system-prompt" in command
    assert "--safe-mode" in command
    assert "--no-session-persistence" in command
    assert calls[1][1] != Path.cwd()
    assert not calls[1][1].exists()


def test_claude_code_rejects_non_subscription_authentication() -> None:
    def runner(
        command: list[str], _cwd: Path, _environment: dict[str, str], _timeout: float
    ) -> subprocess.CompletedProcess[str]:
        return _completed(
            command,
            json.dumps({"loggedIn": True, "authMethod": "apiKey", "subscriptionType": "api"}),
        )

    provider = ClaudeCodeProvider(ClaudeCodeProviderConfig(enabled=True), runner=runner)
    with pytest.raises(IntegrationResponseError, match="subscription authentication"):
        provider.submit(_request())


def test_claude_code_rejects_invalid_structured_output() -> None:
    def runner(
        command: list[str], _cwd: Path, _environment: dict[str, str], _timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if command[1:3] == ["auth", "status"]:
            return _completed(
                command,
                json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "subscriptionType": "pro",
                    }
                ),
            )
        return _completed(command, json.dumps({"result": "not-json"}))

    provider = ClaudeCodeProvider(ClaudeCodeProviderConfig(enabled=True), runner=runner)
    with pytest.raises(IntegrationResponseError, match="invalid response"):
        provider.submit(_request())

from control_plane.domain import (
    ExecutionContext,
    ExecutionResult,
    ProviderResult,
    TaskKind,
    ValidationError,
)


class FakeExecutor:
    """Phase 1 executor that records evidence and deliberately executes no commands."""

    @property
    def name(self) -> str:
        return "fake-no-command-executor"

    def execute(
        self,
        task_kind: TaskKind,
        provider_result: ProviderResult,
        context: ExecutionContext | None = None,
    ) -> ExecutionResult:
        proposed = provider_result.output.get("commands_requested", [])
        if proposed:
            raise ValidationError("Phase 1 executor refuses all command execution")
        return ExecutionResult(
            status="SUCCEEDED",
            summary=f"validated mock evidence for {task_kind.value}",
            evidence={
                "executor": self.name,
                "provider_status": provider_result.status,
                "command_execution_enabled": False,
            },
            commands_executed=(),
        )

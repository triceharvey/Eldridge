from typing import Any

from control_plane.domain import ProviderResult, TaskKind, ValidationError


def validate_provider_result(task_kind: TaskKind, result: ProviderResult) -> None:
    if result.status != "SUCCEEDED" or not isinstance(result.output, dict):
        raise ValidationError("provider did not return a successful structured result")
    output: dict[str, Any] = result.output
    if task_kind == TaskKind.PLAN:
        _require_list(output, "plan")
        _require_list(output, "assumptions")
    elif task_kind == TaskKind.ARCHITECTURE_REVIEW:
        _require_list(output, "findings")
        if output.get("independent_review") is not True:
            raise ValidationError("architecture review lacks independence attestation")
    elif task_kind == TaskKind.IMPLEMENT:
        _require_list(output, "commands_requested")
        if not isinstance(output.get("candidate_revision"), str):
            raise ValidationError("implementation result lacks a candidate revision")
    elif task_kind == TaskKind.TEST:
        if output.get("tests_passed") is not True:
            raise ValidationError("test result does not pass")
        _require_list(output, "failures")
    elif task_kind == TaskKind.SECURITY_REVIEW:
        if output.get("policy_passed") is not True:
            raise ValidationError("security policy did not pass")
        _require_list(output, "findings")
    elif task_kind == TaskKind.CODE_REVIEW:
        if output.get("review_passed") is not True:
            raise ValidationError("code review did not pass")
        _require_list(output, "blocking_findings")


def _require_list(output: dict[str, Any], field: str) -> None:
    if not isinstance(output.get(field), list):
        raise ValidationError(f"provider result field {field} must be a list")

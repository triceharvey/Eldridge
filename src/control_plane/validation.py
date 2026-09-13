from typing import Any

from control_plane.domain import (
    ProviderResult,
    ProviderReviewRejectedError,
    TaskKind,
    ValidationError,
)


def provider_output_contract(task_kind: TaskKind) -> str:
    contracts = {
        TaskKind.PLAN: "Required keys: plan (array) and assumptions (array).",
        TaskKind.ARCHITECTURE_REVIEW: (
            "Required keys: findings (array) and independent_review (literal true)."
        ),
        TaskKind.IMPLEMENT: (
            "Required keys: commands_requested (array), candidate_revision (string), and "
            "tool_requests (array of typed tool proposals)."
        ),
        TaskKind.TEST: (
            "This is pre-execution test planning, not test execution. Required keys: "
            "test_plan_ready (boolean), concerns (array), and tool_requests (non-empty array "
            "of typed deterministic test proposals). Never invent tool results."
        ),
        TaskKind.SECURITY_REVIEW: ("Required keys: policy_passed (boolean) and findings (array)."),
        TaskKind.CODE_REVIEW: (
            "Required keys: review_passed (boolean) and blocking_findings (array)."
        ),
    }
    return contracts[task_kind]


def provider_output_schema(task_kind: TaskKind) -> dict[str, Any]:
    array = {
        "type": "array",
        "maxItems": 8,
        "items": {"type": "string", "maxLength": 500},
    }
    schemas: dict[TaskKind, dict[str, Any]] = {
        TaskKind.PLAN: {
            "type": "object",
            "properties": {"plan": array, "assumptions": array},
            "required": ["plan", "assumptions"],
            "additionalProperties": False,
        },
        TaskKind.ARCHITECTURE_REVIEW: {
            "type": "object",
            "properties": {
                "findings": array,
                "independent_review": {"const": True},
            },
            "required": ["findings", "independent_review"],
            "additionalProperties": False,
        },
        TaskKind.IMPLEMENT: {
            "type": "object",
            "properties": {
                "commands_requested": array,
                "candidate_revision": {"type": "string"},
                "tool_requests": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "enum": [
                                    "LIST_FILES",
                                    "PYTHON_COMPILE",
                                    "PYTHON_UNITTEST",
                                    "WRITE_TEXT_FILE",
                                ]
                            },
                            "path": {"type": "string", "maxLength": 500},
                            "content": {"type": "string", "maxLength": 65_536},
                            "targets": {
                                "type": "array",
                                "maxItems": 64,
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["commands_requested", "candidate_revision", "tool_requests"],
            "additionalProperties": False,
        },
        TaskKind.TEST: {
            "type": "object",
            "properties": {
                "test_plan_ready": {"type": "boolean"},
                "concerns": array,
                "tool_requests": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"enum": ["PYTHON_COMPILE", "PYTHON_UNITTEST"]},
                            "targets": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 64,
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["name", "targets"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["test_plan_ready", "concerns", "tool_requests"],
            "additionalProperties": False,
        },
        TaskKind.SECURITY_REVIEW: {
            "type": "object",
            "properties": {"policy_passed": {"type": "boolean"}, "findings": array},
            "required": ["policy_passed", "findings"],
            "additionalProperties": False,
        },
        TaskKind.CODE_REVIEW: {
            "type": "object",
            "properties": {
                "review_passed": {"type": "boolean"},
                "blocking_findings": array,
            },
            "required": ["review_passed", "blocking_findings"],
            "additionalProperties": False,
        },
    }
    return schemas[task_kind]


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
        _require_list(output, "tool_requests")
        if not isinstance(output.get("candidate_revision"), str):
            raise ValidationError("implementation result lacks a candidate revision")
    elif task_kind == TaskKind.TEST:
        _require_list(output, "concerns")
        _require_list(output, "tool_requests")
        if not isinstance(output.get("test_plan_ready"), bool):
            raise ValidationError("test plan readiness decision must be boolean")
        if output["test_plan_ready"] is not True:
            raise ProviderReviewRejectedError("test plan is not ready", output)
    elif task_kind == TaskKind.SECURITY_REVIEW:
        _require_list(output, "findings")
        if not isinstance(output.get("policy_passed"), bool):
            raise ValidationError("security policy decision must be boolean")
        if output["policy_passed"] is not True:
            raise ProviderReviewRejectedError("security policy did not pass", output)
    elif task_kind == TaskKind.CODE_REVIEW:
        _require_list(output, "blocking_findings")
        if not isinstance(output.get("review_passed"), bool):
            raise ValidationError("code review decision must be boolean")
        if output["review_passed"] is not True:
            raise ProviderReviewRejectedError("code review did not pass", output)


def _require_list(output: dict[str, Any], field: str) -> None:
    if not isinstance(output.get(field), list):
        raise ValidationError(f"provider result field {field} must be a list")

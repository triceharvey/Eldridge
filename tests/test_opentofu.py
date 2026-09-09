from copy import deepcopy
from decimal import Decimal

import pytest

from control_plane.domain import AuthorizationError, ValidationError
from control_plane.opentofu import (
    LocalOpenTofuAdapter,
    OpenTofuPlanPolicy,
    OpenTofuPlanValidator,
)

SAVED_PLAN_DIGEST = "a" * 64
LOCK_DIGEST = "b" * 64


def _policy() -> OpenTofuPlanPolicy:
    return OpenTofuPlanPolicy(
        policy_version="opentofu/local-zero-cost-v1",
        allowed_cli_versions=frozenset({"1.12.0"}),
        allowed_provider_names=frozenset({"terraform.io/builtin/terraform"}),
        allowed_provider_sources=frozenset({"terraform.io/builtin/terraform"}),
        allowed_provider_version_constraints=frozenset(
            {("terraform.io/builtin/terraform", "1.12.0")}
        ),
        allowed_resource_types=frozenset({"terraform_data"}),
        allowed_resource_addresses=frozenset({"terraform_data.eldridge_local"}),
        maximum_monthly_cost_usd=Decimal("0"),
    )


def _plan() -> dict[str, object]:
    return {
        "format_version": "1.0",
        "terraform_version": "1.12.0",
        "errored": False,
        "configuration": {
            "provider_config": {
                "terraform": {
                    "name": "terraform",
                    "full_name": "terraform.io/builtin/terraform",
                    "version_constraint": "1.12.0",
                }
            },
            "root_module": {
                "resources": [
                    {
                        "address": "terraform_data.eldridge_local",
                        "mode": "managed",
                        "type": "terraform_data",
                        "name": "eldridge_local",
                        "provider_config_key": "terraform",
                    }
                ]
            },
        },
        "resource_changes": [
            {
                "address": "terraform_data.eldridge_local",
                "mode": "managed",
                "type": "terraform_data",
                "name": "eldridge_local",
                "provider_name": "terraform.io/builtin/terraform",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {"input": "eldridge-local"},
                    "after_unknown": {},
                    "before_sensitive": False,
                    "after_sensitive": {},
                },
            }
        ],
        "resource_drift": [],
        "output_changes": {},
        "checks": [],
    }


def _validate(plan: dict[str, object] | None = None, *, cost: str = "0"):
    return OpenTofuPlanValidator(_policy()).validate(
        plan or _plan(),
        saved_plan_digest=SAVED_PLAN_DIGEST,
        dependency_lock_digest=LOCK_DIGEST,
        estimated_monthly_cost_usd=cost,
    )


def test_zero_cost_plan_produces_sanitized_digest_bound_evidence() -> None:
    evidence = _validate()

    assert evidence.saved_plan_digest == SAVED_PLAN_DIGEST
    assert evidence.dependency_lock_digest == LOCK_DIGEST
    assert len(evidence.json_plan_digest) == 64
    assert evidence.estimated_monthly_cost_usd == "0"
    assert evidence.resource_changes[0].address == "terraform_data.eldridge_local"
    assert not hasattr(evidence.resource_changes[0], "after")


def test_backward_compatible_minor_format_additions_are_ignored() -> None:
    plan = _plan()
    plan["format_version"] = "1.1"
    plan["future_addition"] = {"ignored": "by-policy-parser"}

    assert _validate(plan).format_version == "1.1"


def test_local_adapter_never_executes_or_contacts_a_target() -> None:
    result = LocalOpenTofuAdapter().simulate(_validate(), idempotency_key="local-plan-1")

    assert result.simulated is True
    assert result.changed is False
    assert result.external_target_contacted is False
    assert result.subprocess_started is False
    assert result.saved_plan_digest == SAVED_PLAN_DIGEST


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda plan: plan.update(format_version="2.0"), "format major"),
        (lambda plan: plan.update(terraform_version="1.13.0"), "not pinned"),
        (lambda plan: plan.update(errored=True), "errored"),
        (lambda plan: plan.update(resource_drift=[{"address": "x"}]), "drift"),
    ],
)
def test_plan_rejects_unsupported_format_version_errors_and_drift(mutator, message) -> None:
    plan = _plan()
    mutator(plan)
    with pytest.raises((AuthorizationError, ValidationError), match=message):
        _validate(plan)


def test_plan_rejects_cost_above_zero() -> None:
    with pytest.raises(AuthorizationError, match="cost ceiling"):
        _validate(cost="0.01")


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["forget"]])
def test_plan_rejects_destructive_actions(actions: list[str]) -> None:
    plan = _plan()
    plan["resource_changes"][0]["change"]["actions"] = actions  # type: ignore[index]
    with pytest.raises(AuthorizationError, match="action"):
        _validate(plan)


def test_plan_rejects_scope_provider_and_type_widening() -> None:
    for key, value, message in (
        ("address", "terraform_data.unapproved", "address"),
        ("type", "docker_container", "type"),
        ("provider_name", "registry.opentofu.org/kreuzwerker/docker", "provider"),
    ):
        plan = _plan()
        plan["resource_changes"][0][key] = value  # type: ignore[index]
        with pytest.raises(AuthorizationError, match=message):
            _validate(plan)


def test_plan_rejects_sensitive_markers_imports_modules_and_provisioners() -> None:
    sensitive = _plan()
    sensitive["resource_changes"][0]["change"]["after_sensitive"] = {  # type: ignore[index]
        "password": True
    }
    with pytest.raises(AuthorizationError, match="sensitive"):
        _validate(sensitive)

    importing = _plan()
    importing["resource_changes"][0]["change"]["importing"] = {"id": "x"}  # type: ignore[index]
    with pytest.raises(AuthorizationError, match="import"):
        _validate(importing)

    module = _plan()
    module["configuration"]["root_module"]["module_calls"] = {"remote": {}}  # type: ignore[index]
    with pytest.raises(AuthorizationError, match="child modules"):
        _validate(module)

    provisioner = _plan()
    provisioner["configuration"]["root_module"]["resources"][0]["provisioners"] = [  # type: ignore[index]
        {"type": "local-exec"}
    ]
    with pytest.raises(AuthorizationError, match="provisioners"):
        _validate(provisioner)


def test_plan_rejects_unpinned_provider_variables_and_configuration_mismatch() -> None:
    provider = _plan()
    provider["configuration"]["provider_config"]["terraform"][  # type: ignore[index]
        "version_constraint"
    ] = ">= 1.0"
    with pytest.raises(AuthorizationError, match="not pinned"):
        _validate(provider)

    variables = _plan()
    variables["variables"] = {"password": {"value": "do-not-retain"}}
    with pytest.raises(AuthorizationError, match="variable values"):
        _validate(variables)

    mismatch = _plan()
    mismatch["configuration"]["root_module"]["resources"] = []  # type: ignore[index]
    with pytest.raises(AuthorizationError, match="do not match"):
        _validate(mismatch)


def test_plan_rejects_nonpassing_checks_and_changed_outputs() -> None:
    failed_check = _plan()
    failed_check["checks"] = [{"status": "fail"}]
    with pytest.raises(AuthorizationError, match="non-passing"):
        _validate(failed_check)

    output = _plan()
    output["output_changes"] = {"endpoint": {"actions": ["create"]}}
    with pytest.raises(AuthorizationError, match="output changes"):
        _validate(output)


def test_plan_rejects_invalid_digests_and_oversized_input() -> None:
    validator = OpenTofuPlanValidator(_policy())
    with pytest.raises(ValidationError, match="saved plan digest"):
        validator.validate(
            _plan(),
            saved_plan_digest="invalid",
            dependency_lock_digest=LOCK_DIGEST,
            estimated_monthly_cost_usd="0",
        )
    tiny_policy = deepcopy(_policy())
    object.__setattr__(tiny_policy, "max_json_bytes", 1)
    with pytest.raises(ValidationError, match="size boundary"):
        OpenTofuPlanValidator(tiny_policy).validate(
            _plan(),
            saved_plan_digest=SAVED_PLAN_DIGEST,
            dependency_lock_digest=LOCK_DIGEST,
            estimated_monthly_cost_usd="0",
        )

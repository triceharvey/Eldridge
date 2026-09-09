from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from control_plane.domain import AuthorizationError, ValidationError

PlanAction = tuple[str, ...]


@dataclass(frozen=True)
class OpenTofuPlanPolicy:
    policy_version: str
    allowed_cli_versions: frozenset[str]
    allowed_provider_names: frozenset[str]
    allowed_provider_sources: frozenset[str]
    allowed_provider_version_constraints: frozenset[tuple[str, str]]
    allowed_resource_types: frozenset[str]
    allowed_resource_addresses: frozenset[str]
    allowed_actions: frozenset[PlanAction] = frozenset({("no-op",), ("create",), ("update",)})
    max_resource_changes: int = 50
    max_json_bytes: int = 1_000_000
    maximum_monthly_cost_usd: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValidationError("OpenTofu policy version is required")
        required_allowlists = (
            self.allowed_cli_versions,
            self.allowed_provider_names,
            self.allowed_provider_sources,
            self.allowed_provider_version_constraints,
            self.allowed_resource_types,
            self.allowed_resource_addresses,
            self.allowed_actions,
        )
        if any(not values for values in required_allowlists):
            raise ValidationError("OpenTofu policy allowlists cannot be empty")
        if self.max_resource_changes < 1 or self.max_resource_changes > 1_000:
            raise ValidationError("OpenTofu resource-change limit is invalid")
        if self.max_json_bytes < 1 or self.max_json_bytes > 10_000_000:
            raise ValidationError("OpenTofu JSON size limit is invalid")
        if self.maximum_monthly_cost_usd < 0:
            raise ValidationError("OpenTofu cost ceiling cannot be negative")


@dataclass(frozen=True)
class OpenTofuResourceChangeEvidence:
    address: str
    resource_type: str
    provider_name: str
    actions: PlanAction


@dataclass(frozen=True)
class ValidatedOpenTofuPlan:
    policy_version: str
    format_version: str
    cli_version: str
    saved_plan_digest: str
    json_plan_digest: str
    dependency_lock_digest: str
    estimated_monthly_cost_usd: str
    resource_changes: tuple[OpenTofuResourceChangeEvidence, ...]
    drift_detected: bool
    contains_sensitive_values: bool


@dataclass(frozen=True)
class LocalOpenTofuSimulation:
    operation_reference: str
    saved_plan_digest: str
    json_plan_digest: str
    simulated: bool
    changed: bool
    external_target_contacted: bool
    subprocess_started: bool


class OpenTofuPlanValidator:
    """Validate bounded `tofu show -json` evidence without retaining plan values."""

    def __init__(self, policy: OpenTofuPlanPolicy) -> None:
        self.policy = policy

    def validate(
        self,
        plan: Mapping[str, Any],
        *,
        saved_plan_digest: str,
        dependency_lock_digest: str,
        estimated_monthly_cost_usd: str,
    ) -> ValidatedOpenTofuPlan:
        self._validate_digest(saved_plan_digest, "saved plan")
        self._validate_digest(dependency_lock_digest, "dependency lock")
        canonical = self._canonical_json(plan)
        if len(canonical) > self.policy.max_json_bytes:
            raise ValidationError("OpenTofu JSON plan exceeds its size boundary")
        json_plan_digest = hashlib.sha256(canonical).hexdigest()

        format_version = self._required_string(plan, "format_version")
        if format_version.split(".", 1)[0] != "1":
            raise ValidationError("unsupported OpenTofu JSON format major version")
        cli_version = self._required_string(plan, "terraform_version")
        if cli_version not in self.policy.allowed_cli_versions:
            raise AuthorizationError("OpenTofu CLI version is not pinned by policy")
        if plan.get("errored") is not False:
            raise ValidationError("errored or incomplete OpenTofu plan is not eligible")
        variables = plan.get("variables", {})
        if not isinstance(variables, dict) or variables:
            raise AuthorizationError("OpenTofu variable values are disabled for the local profile")
        if self._contains_sensitive_values(plan):
            raise AuthorizationError("OpenTofu JSON plan contains sensitive values")

        cost = self._parse_cost(estimated_monthly_cost_usd)
        if cost > self.policy.maximum_monthly_cost_usd:
            raise AuthorizationError("OpenTofu plan exceeds the approved cost ceiling")

        drift = plan.get("resource_drift", [])
        if not isinstance(drift, list):
            raise ValidationError("OpenTofu resource drift must be a list")
        if drift:
            raise AuthorizationError("OpenTofu plan contains unresolved resource drift")

        configured_addresses = self._validate_configuration(plan.get("configuration"))
        self._validate_checks(plan.get("checks", []))
        self._validate_output_changes(plan.get("output_changes", {}))
        changes = self._validate_resource_changes(plan.get("resource_changes"))
        if {change.address for change in changes} != configured_addresses:
            raise AuthorizationError(
                "OpenTofu configured resources do not match resource-change evidence"
            )
        return ValidatedOpenTofuPlan(
            policy_version=self.policy.policy_version,
            format_version=format_version,
            cli_version=cli_version,
            saved_plan_digest=saved_plan_digest,
            json_plan_digest=json_plan_digest,
            dependency_lock_digest=dependency_lock_digest,
            estimated_monthly_cost_usd=str(cost),
            resource_changes=changes,
            drift_detected=False,
            contains_sensitive_values=False,
        )

    def _validate_resource_changes(
        self, raw_changes: object
    ) -> tuple[OpenTofuResourceChangeEvidence, ...]:
        if not isinstance(raw_changes, list) or not raw_changes:
            raise ValidationError("OpenTofu plan requires resource-change evidence")
        if len(raw_changes) > self.policy.max_resource_changes:
            raise AuthorizationError("OpenTofu plan exceeds its resource-change limit")
        evidence: list[OpenTofuResourceChangeEvidence] = []
        seen_addresses: set[str] = set()
        for raw_change in raw_changes:
            if not isinstance(raw_change, dict):
                raise ValidationError("OpenTofu resource change must be an object")
            address = self._required_string(raw_change, "address")
            resource_type = self._required_string(raw_change, "type")
            provider_name = self._required_string(raw_change, "provider_name")
            if address in seen_addresses:
                raise ValidationError("OpenTofu plan contains duplicate resource addresses")
            seen_addresses.add(address)
            if raw_change.get("mode") != "managed":
                raise AuthorizationError("OpenTofu plan contains a non-managed resource")
            if address not in self.policy.allowed_resource_addresses:
                raise AuthorizationError("OpenTofu resource address exceeds policy")
            if resource_type not in self.policy.allowed_resource_types:
                raise AuthorizationError("OpenTofu resource type exceeds policy")
            if provider_name not in self.policy.allowed_provider_names:
                raise AuthorizationError("OpenTofu provider exceeds policy")
            if raw_change.get("deposed") is not None or raw_change.get("previous_address"):
                raise AuthorizationError("OpenTofu moved or deposed resources require review")
            change = raw_change.get("change")
            if not isinstance(change, dict):
                raise ValidationError("OpenTofu resource change body is invalid")
            raw_actions = change.get("actions")
            if not isinstance(raw_actions, list) or not all(
                isinstance(item, str) for item in raw_actions
            ):
                raise ValidationError("OpenTofu resource actions are invalid")
            actions = tuple(raw_actions)
            if actions not in self.policy.allowed_actions:
                raise AuthorizationError("OpenTofu resource action exceeds policy")
            if change.get("importing") is not None or change.get("generated_config") is not None:
                raise AuthorizationError("OpenTofu import or generated configuration is forbidden")
            if self._contains_sensitive_marker(change.get("before_sensitive")) or (
                self._contains_sensitive_marker(change.get("after_sensitive"))
            ):
                raise AuthorizationError("OpenTofu JSON plan contains sensitive values")
            evidence.append(
                OpenTofuResourceChangeEvidence(
                    address=address,
                    resource_type=resource_type,
                    provider_name=provider_name,
                    actions=actions,
                )
            )
        return tuple(evidence)

    def _validate_configuration(self, configuration: object) -> set[str]:
        if not isinstance(configuration, dict):
            raise ValidationError("OpenTofu plan lacks configuration evidence")
        provider_config = configuration.get("provider_config")
        if not isinstance(provider_config, dict) or not provider_config:
            raise ValidationError("OpenTofu plan lacks provider configuration evidence")
        provider_keys: set[str] = set()
        for provider_key, raw_provider in provider_config.items():
            if not isinstance(provider_key, str) or not provider_key:
                raise ValidationError("OpenTofu provider configuration key is invalid")
            if not isinstance(raw_provider, dict):
                raise ValidationError("OpenTofu provider configuration is invalid")
            full_name = self._required_string(raw_provider, "full_name")
            if full_name not in self.policy.allowed_provider_sources:
                raise AuthorizationError("OpenTofu provider source exceeds policy")
            constraint = self._required_string(raw_provider, "version_constraint")
            if (
                full_name,
                constraint,
            ) not in self.policy.allowed_provider_version_constraints:
                raise AuthorizationError("OpenTofu provider version is not pinned by policy")
            provider_keys.add(provider_key)
        root_module = configuration.get("root_module")
        if not isinstance(root_module, dict):
            raise ValidationError("OpenTofu root-module configuration is required")
        if root_module.get("module_calls"):
            raise AuthorizationError("OpenTofu child modules are disabled for the local profile")
        resources = root_module.get("resources", [])
        if not isinstance(resources, list):
            raise ValidationError("OpenTofu configuration resources are invalid")
        configured_addresses: set[str] = set()
        for resource in resources:
            if not isinstance(resource, dict):
                raise ValidationError("OpenTofu configuration resource is invalid")
            if resource.get("provisioners"):
                raise AuthorizationError("OpenTofu provisioners are forbidden")
            address = self._required_string(resource, "address")
            resource_type = self._required_string(resource, "type")
            provider_key = self._required_string(resource, "provider_config_key")
            if resource.get("mode") != "managed":
                raise AuthorizationError("OpenTofu configuration contains a non-managed resource")
            if address not in self.policy.allowed_resource_addresses:
                raise AuthorizationError("OpenTofu configuration address exceeds policy")
            if resource_type not in self.policy.allowed_resource_types:
                raise AuthorizationError("OpenTofu configuration type exceeds policy")
            if provider_key not in provider_keys:
                raise AuthorizationError("OpenTofu resource references an unapproved provider")
            if address in configured_addresses:
                raise ValidationError("OpenTofu configuration contains a duplicate address")
            configured_addresses.add(address)
        return configured_addresses

    @staticmethod
    def _validate_checks(checks: object) -> None:
        if not isinstance(checks, list):
            raise ValidationError("OpenTofu checks evidence must be a list")
        if any(not isinstance(check, dict) or check.get("status") != "pass" for check in checks):
            raise AuthorizationError("OpenTofu plan contains a non-passing check")

    def _validate_output_changes(self, output_changes: object) -> None:
        if not isinstance(output_changes, dict):
            raise ValidationError("OpenTofu output changes must be an object")
        for raw_output in output_changes.values():
            if not isinstance(raw_output, dict):
                raise ValidationError("OpenTofu output change is invalid")
            raw_actions = raw_output.get("actions")
            if raw_actions is None and isinstance(raw_output.get("change"), dict):
                raw_actions = raw_output["change"].get("actions")
            if raw_actions != ["no-op"]:
                raise AuthorizationError("OpenTofu output changes are disabled")
            if self._contains_sensitive_marker(raw_output):
                raise AuthorizationError("OpenTofu output contains sensitive values")

    @staticmethod
    def _contains_sensitive_marker(value: object) -> bool:
        if value is True:
            return True
        if isinstance(value, dict):
            return any(
                OpenTofuPlanValidator._contains_sensitive_marker(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(OpenTofuPlanValidator._contains_sensitive_marker(item) for item in value)
        return False

    @staticmethod
    def _contains_sensitive_values(value: object) -> bool:
        sensitive_keys = {
            "sensitive",
            "sensitive_values",
            "before_sensitive",
            "after_sensitive",
        }
        if isinstance(value, dict):
            for key, item in value.items():
                if key in sensitive_keys and OpenTofuPlanValidator._contains_sensitive_marker(item):
                    return True
                if OpenTofuPlanValidator._contains_sensitive_values(item):
                    return True
        elif isinstance(value, list):
            return any(OpenTofuPlanValidator._contains_sensitive_values(item) for item in value)
        return False

    @staticmethod
    def _required_string(values: Mapping[str, Any], key: str) -> str:
        value = values.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ValidationError(f"OpenTofu {key} is required")
        return value

    @staticmethod
    def _validate_digest(value: str, label: str) -> None:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValidationError(f"OpenTofu {label} digest must be lowercase SHA-256")

    @staticmethod
    def _canonical_json(plan: Mapping[str, Any]) -> bytes:
        try:
            return json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError) as exc:
            raise ValidationError("OpenTofu JSON plan is not canonicalizable") from exc

    @staticmethod
    def _parse_cost(value: str) -> Decimal:
        try:
            cost = Decimal(value)
        except InvalidOperation as exc:
            raise ValidationError("OpenTofu cost estimate is invalid") from exc
        if not cost.is_finite() or cost < 0:
            raise ValidationError("OpenTofu cost estimate is invalid")
        return cost


class LocalOpenTofuAdapter:
    """No-change adapter for validated local plan evidence; it starts no subprocess."""

    adapter_id = "opentofu-local-simulation-v1"
    requires_credentials = False

    def simulate(
        self, evidence: ValidatedOpenTofuPlan, *, idempotency_key: str
    ) -> LocalOpenTofuSimulation:
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValidationError("OpenTofu simulation idempotency key is invalid")
        if evidence.estimated_monthly_cost_usd != "0":
            raise AuthorizationError("local OpenTofu simulation must remain zero cost")
        operation_digest = hashlib.sha256(
            f"{evidence.saved_plan_digest}:{idempotency_key}".encode()
        ).hexdigest()
        return LocalOpenTofuSimulation(
            operation_reference=f"opentofu-local-simulation:{operation_digest}",
            saved_plan_digest=evidence.saved_plan_digest,
            json_plan_digest=evidence.json_plan_digest,
            simulated=True,
            changed=False,
            external_target_contacted=False,
            subprocess_started=False,
        )

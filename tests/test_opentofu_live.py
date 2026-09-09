import hashlib
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from control_plane.opentofu import (
    LocalOpenTofuAdapter,
    OpenTofuPlanPolicy,
    OpenTofuPlanValidator,
)

TOFU = shutil.which("tofu")
FIXTURE = Path(__file__).parents[1] / "deployment" / "opentofu" / "local-fixture"


def _run_tofu(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    assert TOFU is not None
    return subprocess.run(  # noqa: S603 - resolved binary is version-pinned by the fixture
        [TOFU, *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.skipif(TOFU is None, reason="pinned local OpenTofu binary is not installed")
def test_real_builtin_only_plan_validates_without_apply(tmp_path: Path) -> None:
    assert TOFU is not None
    shutil.copy2(FIXTURE / "main.tf", tmp_path / "main.tf")
    shutil.copy2(FIXTURE / ".terraform.lock.hcl", tmp_path / ".terraform.lock.hcl")
    _run_tofu(
        "init",
        "-backend=false",
        "-lockfile=readonly",
        "-input=false",
        cwd=tmp_path,
    )
    _run_tofu(
        "validate",
        "-no-color",
        cwd=tmp_path,
    )
    plan_path = tmp_path / "eldridge-local.tfplan"
    _run_tofu(
        "plan",
        "-refresh=false",
        "-input=false",
        "-no-color",
        f"-out={plan_path}",
        cwd=tmp_path,
    )
    shown = _run_tofu(
        "show",
        "-json",
        str(plan_path),
        cwd=tmp_path,
    )
    policy = OpenTofuPlanPolicy(
        policy_version="opentofu/local-zero-cost-v1",
        allowed_cli_versions=frozenset({"1.12.6"}),
        allowed_provider_names=frozenset({"terraform.io/builtin/terraform"}),
        allowed_provider_sources=frozenset({"terraform.io/builtin/terraform"}),
        allowed_provider_version_constraints=frozenset(
            {("terraform.io/builtin/terraform", "builtin:1.12.6")}
        ),
        allowed_resource_types=frozenset({"terraform_data"}),
        allowed_resource_addresses=frozenset({"terraform_data.eldridge_local"}),
        maximum_monthly_cost_usd=Decimal("0"),
    )
    evidence = OpenTofuPlanValidator(policy).validate(
        json.loads(shown.stdout),
        saved_plan_digest=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        dependency_lock_digest=hashlib.sha256(
            (tmp_path / ".terraform.lock.hcl").read_bytes()
        ).hexdigest(),
        estimated_monthly_cost_usd="0",
    )
    result = LocalOpenTofuAdapter().simulate(evidence, idempotency_key="live-local-plan")

    assert evidence.format_version == "1.2"
    assert evidence.cli_version == "1.12.6"
    assert evidence.resource_changes[0].actions == ("create",)
    assert result.changed is False
    assert result.subprocess_started is False
    assert not list(tmp_path.glob("*.tfstate*"))

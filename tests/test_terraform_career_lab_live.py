import json
import shutil
import subprocess
from pathlib import Path

import pytest

LAB = Path(__file__).parents[1] / "deployment" / "iac-career-lab"
ROOT = Path("examples/local")
ENGINE_VERSIONS = {
    "terraform": "1.16.2",
    "tofu": "1.12.6",
}
REVISION_A = "a" * 40
REVISION_B = "b" * 40


def _copy_lab(tmp_path: Path) -> Path:
    destination = tmp_path / "career-lab"
    shutil.copytree(LAB, destination)
    return destination / ROOT


def _run(
    executable: str,
    *arguments: str,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - executable is resolved from a fixed allowlist
        [executable, *arguments],
        cwd=cwd,
        check=check,
        capture_output=True,
        timeout=30,
    )


def _variables(environment: str, revision: str) -> tuple[str, ...]:
    return (
        "-var=project_name=eldridge",
        f"-var=environment={environment}",
        f"-var=revision={revision}",
        "-var=monthly_budget_usd=0",
        '-var=labels={owner="triceharvey",purpose="career-lab"}',
    )


@pytest.mark.parametrize("engine", sorted(ENGINE_VERSIONS))
def test_reusable_module_produces_bounded_plan(engine: str, tmp_path: Path) -> None:
    executable = shutil.which(engine)
    if executable is None:
        pytest.skip(f"pinned local {engine} binary is not installed")
    root = _copy_lab(tmp_path)

    version = json.loads(_run(executable, "version", "-json", cwd=root).stdout)
    assert version["terraform_version"] == ENGINE_VERSIONS[engine]
    _run(executable, "fmt", "-check", "-recursive", cwd=root.parents[1])
    _run(executable, "init", "-backend=false", "-lockfile=readonly", "-input=false", cwd=root)
    _run(executable, "validate", "-no-color", cwd=root)

    plan_path = root / f"{engine}-career-lab.tfplan"
    _run(
        executable,
        "plan",
        "-refresh=false",
        "-input=false",
        "-no-color",
        f"-out={plan_path}",
        *_variables("development", REVISION_A),
        cwd=root,
    )
    shown = json.loads(_run(executable, "show", "-json", str(plan_path), cwd=root).stdout)

    change = shown["resource_changes"][0]
    assert change["address"] == "module.eldridge_profile.terraform_data.profile"
    assert change["change"]["actions"] == ["create"]
    assert shown["output_changes"]["profile"]["after_unknown"] is True
    assert not list(root.glob("*.tfstate*"))


def test_terraform_workspaces_detect_change_and_destroy_local_state(tmp_path: Path) -> None:
    executable = shutil.which("terraform")
    if executable is None:
        pytest.skip("pinned local terraform binary is not installed")
    root = _copy_lab(tmp_path)
    _run(executable, "init", "-input=false", "-lockfile=readonly", cwd=root)

    _run(
        executable,
        "apply",
        "-auto-approve",
        "-input=false",
        *_variables("development", REVISION_A),
        cwd=root,
    )
    default_output = json.loads(_run(executable, "output", "-json", cwd=root).stdout)
    assert default_output["profile"]["value"]["environment"] == "development"
    assert (root / "terraform.tfstate").is_file()

    _run(executable, "workspace", "new", "staging", cwd=root)
    _run(
        executable,
        "apply",
        "-auto-approve",
        "-input=false",
        *_variables("staging", REVISION_A),
        cwd=root,
    )
    staging_state = root / "terraform.tfstate.d" / "staging" / "terraform.tfstate"
    assert staging_state.is_file()
    assert (
        json.loads(staging_state.read_text())["resources"][0]["module"] == "module.eldridge_profile"
    )

    changed_plan = root / "revision-change.tfplan"
    change = _run(
        executable,
        "plan",
        "-detailed-exitcode",
        "-input=false",
        "-no-color",
        f"-out={changed_plan}",
        *_variables("staging", REVISION_B),
        cwd=root,
        check=False,
    )
    assert change.returncode == 2
    shown = json.loads(_run(executable, "show", "-json", str(changed_plan), cwd=root).stdout)
    assert shown["resource_changes"][0]["change"]["actions"] == ["update"]

    _run(
        executable,
        "destroy",
        "-auto-approve",
        "-input=false",
        *_variables("staging", REVISION_A),
        cwd=root,
    )
    assert _run(executable, "state", "list", cwd=root).stdout == b""
    _run(executable, "workspace", "select", "default", cwd=root)
    _run(
        executable,
        "destroy",
        "-auto-approve",
        "-input=false",
        *_variables("development", REVISION_A),
        cwd=root,
    )
    assert _run(executable, "state", "list", cwd=root).stdout == b""


@pytest.mark.parametrize("engine", sorted(ENGINE_VERSIONS))
def test_zero_dollar_validation_fails_closed(engine: str, tmp_path: Path) -> None:
    executable = shutil.which(engine)
    if executable is None:
        pytest.skip(f"pinned local {engine} binary is not installed")
    root = _copy_lab(tmp_path)
    _run(executable, "init", "-backend=false", "-lockfile=readonly", "-input=false", cwd=root)

    result = _run(
        executable,
        "plan",
        "-input=false",
        "-no-color",
        "-var=project_name=eldridge",
        "-var=environment=development",
        f"-var=revision={REVISION_A}",
        "-var=monthly_budget_usd=1",
        cwd=root,
        check=False,
    )
    assert result.returncode == 1
    assert b"requires a monthly_budget_usd value of zero" in result.stderr

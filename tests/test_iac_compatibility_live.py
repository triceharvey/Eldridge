import json
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parents[1] / "deployment" / "iac-compatibility" / "local-fixture"
ENGINE_VERSIONS = {
    "terraform": "1.16.2",
    "tofu": "1.12.6",
}


def _run_engine(
    executable: str,
    *arguments: str,
    cwd: Path,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - executable is resolved from a fixed allowlist
        [executable, *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize("engine", sorted(ENGINE_VERSIONS))
def test_builtin_plan_is_compatible_without_apply(engine: str, tmp_path: Path) -> None:
    executable = shutil.which(engine)
    if executable is None:
        pytest.skip(f"pinned local {engine} binary is not installed")

    version = json.loads(_run_engine(executable, "version", "-json", cwd=tmp_path).stdout)
    assert version["terraform_version"] == ENGINE_VERSIONS[engine]

    shutil.copy2(FIXTURE / "main.tf", tmp_path / "main.tf")
    shutil.copy2(FIXTURE / ".terraform.lock.hcl", tmp_path / ".terraform.lock.hcl")
    _run_engine(
        executable,
        "init",
        "-backend=false",
        "-lockfile=readonly",
        "-input=false",
        cwd=tmp_path,
    )
    _run_engine(executable, "validate", "-no-color", cwd=tmp_path)

    plan_path = tmp_path / f"{engine}-compatibility.tfplan"
    _run_engine(
        executable,
        "plan",
        "-refresh=false",
        "-input=false",
        "-no-color",
        f"-out={plan_path}",
        cwd=tmp_path,
    )
    shown = json.loads(
        _run_engine(executable, "show", "-json", str(plan_path), cwd=tmp_path).stdout
    )

    change = shown["resource_changes"][0]
    assert shown["terraform_version"] == ENGINE_VERSIONS[engine]
    assert change["address"] == "terraform_data.eldridge_compatibility"
    assert change["type"] == "terraform_data"
    assert change["change"]["actions"] == ["create"]
    assert not list(tmp_path.glob("*.tfstate*"))

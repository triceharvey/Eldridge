import shutil
import subprocess
from pathlib import Path

import pytest

PROFILE = Path(__file__).parents[1] / "deployment" / "oci-canary"


def test_profile_is_bounded_to_always_free_capacity() -> None:
    configuration = (PROFILE / "main.tf").read_text()
    versions = (PROFILE / "versions.tf").read_text()

    assert 'version = "8.29.0"' in versions
    assert 'shape                      = "VM.Standard.A1.Flex"' in configuration
    assert "ocpus                      = 2" in configuration
    assert "memory_gb                  = 12" in configuration
    assert "boot_volume_gb             = 50" in configuration
    assert "var.confirmed_home_region" in configuration
    assert "var.confirmed_always_free_eligibility" in configuration


def test_profile_exposes_only_web_ingress_and_private_backups() -> None:
    configuration = (PROFILE / "main.tf").read_text()
    outputs = (PROFILE / "outputs.tf").read_text()

    assert "allowed_public_tcp_ports = [80, 443]" in configuration
    assert "NoPublicAccess" in configuration
    assert 'versioning     = "Enabled"' in configuration
    assert "SSH and PostgreSQL are deliberately absent" in outputs
    assert "port 22" not in configuration
    assert "port 5432" not in configuration


def test_example_fails_closed_without_operator_confirmation() -> None:
    example = (PROFILE / "terraform.tfvars.example").read_text()

    assert "confirmed_home_region          = false" in example
    assert "confirmed_always_free_eligibility = false" in example
    assert "replace-me" in example


def test_mock_provider_plan_passes_without_oci_credentials(tmp_path: Path) -> None:
    executable = shutil.which("tofu")
    if executable is None:
        pytest.skip("pinned local OpenTofu binary is not installed")

    profile = tmp_path / "oci-canary"
    shutil.copytree(PROFILE, profile, ignore=shutil.ignore_patterns(".terraform"))
    subprocess.run(  # noqa: S603 - executable is resolved from the fixed OpenTofu name
        [executable, "init", "-backend=false", "-input=false", "-lockfile=readonly"],
        cwd=profile,
        check=True,
        capture_output=True,
        timeout=60,
    )
    result = subprocess.run(  # noqa: S603 - executable is resolved from the fixed OpenTofu name
        [executable, "test", "-no-color"],
        cwd=profile,
        check=True,
        capture_output=True,
        timeout=60,
    )

    assert b"Success! 2 passed, 0 failed." in result.stdout
    assert not list(profile.glob("*.tfstate*"))
